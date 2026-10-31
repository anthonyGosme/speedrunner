<?php
// speedrunner daemon for PHP tools: serves each invocation of one tool from
// a forked child. It speaks the protocol of speedrunner_daemon.py, to the
// same client; see there for the wire format and the model (prefork spare,
// control socketpair, rule n°1). What differs for PHP:
//
// - Nothing of the tool runs in the daemon. The gain is the interpreter
//   start (php.ini, extensions) and OPcache: the client starts us with
//   opcache.enable_cli=1, its shared memory is inherited by every child, and
//   what one child compiles serves the next ones, as for PHP-FPM workers.
//   Compiling or declaring in the parent is not an option: OPcache binds
//   simple classes early, and a later plain `require` of the file (Composer's
//   autoload_static.php) is a fatal redeclaration. Freshness is OPcache's
//   (validate_timestamps, revalidate_freq=0) plus a fingerprint of the
//   interpreter and its ini files.
// - No exit code from a shutdown function: the child exits as it would, and
//   the parent's wait status (W) is what the client gets.
// - PHP has no fd numbers nor dup2: sockets, fd passing and dup2 go through
//   FFI to libc.
// - The tool is included at the top level of this file, so that its
//   top-level variables are globals; $argv and $_SERVER are rebuilt as the
//   interpreter would have them. An uncaught exception is printed as PHP
//   would, without this file's frame.
//
// Started by the client, with the tool's own interpreter:
//
//   php -d opcache.enable_cli=1 ... speedrunner_daemon.php --tool NAME
//       --script PATH --socket PATH [--lock-fd N] [--origin TEXT]
//
// Linux and macOS: the C side differs, see libc(). Needs the pcntl, posix and
// FFI extensions.

namespace Speedrunner {

final class Daemon
{
    const MAGIC = 'HS1';
    const HANDSHAKE_TIMEOUT_S = 5.0;
    const LOCK_WAIT_S = 10.0;
    const STARTUP_ENV_KEYS = ['PHPRC', 'PHP_INI_SCAN_DIR'];
    // Linux (x86-64 and arm64 alike), or macOS: no SOCK_NONBLOCK nor
    // SOCK_CLOEXEC there (fcntl instead), another cmsghdr, LOCAL_PEERCRED.
    const MACOS = PHP_OS_FAMILY === 'Darwin';
    const AF_UNIX = 1, SOCK_STREAM = 1;
    const SOCK_NONBLOCK = self::MACOS ? 0 : 0o4000, SOCK_CLOEXEC = self::MACOS ? 0 : 0o2000000;
    const SOL_SOCKET = self::MACOS ? 0xffff : 1, SO_PEERCRED = 17, SCM_RIGHTS = 1;
    const SOL_LOCAL = 0, LOCAL_PEERCRED = 1;
    const MSG_NOSIGNAL = self::MACOS ? 0x80000 : 0x4000, MSG_CMSG_CLOEXEC = self::MACOS ? 0 : 0x40000000;
    const POLLIN = 1, F_SETFD = 2, FD_CLOEXEC = 1, LOCK_EX = 2, LOCK_NB = 4;
    const F_SETFL = 4, O_NONBLOCK = self::MACOS ? 4 : 0o4000, F_GETPATH = 50;
    const EINTR = 4, EAGAIN = self::MACOS ? 35 : 11;
    // struct cmsghdr: size_t len then two ints, 8-aligned (Linux); a 32-bit
    // len, 4-aligned (macOS).
    const CMSG_HDR = self::MACOS ? 12 : 16, CMSG_ALIGN = self::MACOS ? 4 : 8;

    /** The script the top level includes, once a child is ready. */
    public static ?string $run = null;

    private static \FFI $c;
    private static int $log = 2;
    private static int $parentPid;
    private static array $state = [];

    private static function libc(): \FFI
    {
        $platform = self::MACOS ? '
            struct msghdr { void *msg_name; socklen_t msg_namelen; struct iovec *msg_iov;
                            int msg_iovlen; void *msg_control; socklen_t msg_controllen;
                            int msg_flags; };
            struct sockaddr_un { unsigned char sun_len; unsigned char sun_family; char sun_path[104]; };
            struct xucred { unsigned int cr_version; unsigned int cr_uid; short cr_ngroups;
                            unsigned int cr_groups[16]; };
            int accept(int, void *, void *);
            int *__error(void);
            void *dlsym(void *, const char *);
            int open(const char *, int);
            void _exit(int);
        ' : '
            struct msghdr { void *msg_name; socklen_t msg_namelen; struct iovec *msg_iov;
                            unsigned long msg_iovlen; void *msg_control;
                            unsigned long msg_controllen; int msg_flags; };
            struct sockaddr_un { unsigned short sun_family; char sun_path[108]; };
            struct ucred { int pid; unsigned int uid; unsigned int gid; };
            int accept4(int, void *, void *, int);
            int *__errno_location(void);
        ';
        return self::$c ??= \FFI::cdef('
            typedef unsigned int socklen_t;
            struct iovec { void *iov_base; unsigned long iov_len; };
            struct pollfd { int fd; short events; short revents; };
            ' . $platform . '
            int socket(int, int, int);
            int socketpair(int, int, int, int *);
            int bind(int, const struct sockaddr_un *, socklen_t);
            int listen(int, int);
            long recvmsg(int, struct msghdr *, int);
            long sendmsg(int, const struct msghdr *, int);
            long read(int, void *, unsigned long);
            long write(int, const void *, unsigned long);
            int close(int);
            int dup(int);
            int dup2(int, int);
            int fchdir(int);
            int flock(int, int);
            int fcntl(int, int, int);
            int poll(struct pollfd *, unsigned long, int);
            int getsockopt(int, int, int, void *, socklen_t *);
            long signal(int, long);
        ', self::MACOS ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
    }

    private static function errno(): int
    {
        return self::MACOS ? self::libc()->__error()[0] : self::libc()->__errno_location()[0];
    }

    /** macOS: close-on-exec, and the given O_NONBLOCK, as SOCK_* flags set them on Linux. */
    private static function socketFlags(int $fd, bool $nonblock): int
    {
        if (self::MACOS && $fd >= 0) {
            self::libc()->fcntl($fd, self::F_SETFD, self::FD_CLOEXEC);
            self::libc()->fcntl($fd, self::F_SETFL, $nonblock ? self::O_NONBLOCK : 0);
        }
        return $fd;
    }

    /** accept4(SOCK_CLOEXEC): blocking, close-on-exec. On macOS the
     *  accepted socket would inherit the listener's O_NONBLOCK. */
    private static function accept(int $lsock): int
    {
        $c = self::libc();
        return self::MACOS ? self::socketFlags($c->accept($lsock, null, null), false)
                           : $c->accept4($lsock, null, null, self::SOCK_CLOEXEC);
    }

    /** [uid, gid, groups] of the peer; groups null on Linux, where the
     *  request's are compared with ours. */
    private static function peerCreds(int $conn): array
    {
        $c = self::libc();
        $cred = $c->new(self::MACOS ? 'struct xucred' : 'struct ucred');
        $len = $c->new('socklen_t');
        $len->cdata = \FFI::sizeof($cred);
        if (self::MACOS) {
            $c->getsockopt($conn, self::SOL_LOCAL, self::LOCAL_PEERCRED, \FFI::addr($cred), \FFI::addr($len));
            $groups = [];
            for ($i = 0; $i < min(16, max(0, $cred->cr_ngroups)); $i++) {
                $groups[] = $cred->cr_groups[$i];
            }
            return [$cred->cr_uid, $groups[0] ?? -1, $groups];
        }
        $c->getsockopt($conn, self::SOL_SOCKET, self::SO_PEERCRED, \FFI::addr($cred), \FFI::addr($len));
        return [$cred->uid, $cred->gid, null];
    }

    /** Our groups as the kernel has them: on macOS, posix_getgroups() may
     *  give the user's, resolved anew; ours are read as a peer's are. */
    private static function ownGroups(): array
    {
        if (!self::MACOS) {
            return posix_getgroups();
        }
        static $groups = null;
        if ($groups === null) {
            $sv = self::libc()->new('int[2]');
            self::libc()->socketpair(self::AF_UNIX, self::SOCK_STREAM, 0, $sv);
            $groups = self::peerCreds($sv[0])[2];
            self::libc()->close($sv[0]);
            self::libc()->close($sv[1]);
        }
        return $groups;
    }

    /** A path naming the directory open as fd. */
    private static function dirPath(int $fd): string
    {
        if (!self::MACOS) {
            return "/proc/self/fd/$fd";
        }
        static $ffi = null;
        $ffi ??= \FFI::cdef('int fcntl(int, int, char *);', '/usr/lib/libSystem.B.dylib');
        $buf = $ffi->new('char[1024]');
        return $ffi->fcntl($fd, self::F_GETPATH, $buf) === 0 ? \FFI::string($buf) : '/nonexistent';
    }

    /**
     * macOS: whether a forked child of ours would be killed for using
     * CoreFoundation, because this process used it (an extension, at
     * startup: nothing of the tool runs here). See cf_unsafe in
     * speedrunner_daemon.py; the child leaves by _exit, not by a crash.
     */
    private static function cfUnsafe(): bool
    {
        if (!self::MACOS) {
            return false;
        }
        $pid = pcntl_fork();
        if ($pid === 0) {
            $c = self::libc();
            $exit = \FFI::cast('long', $c->dlsym(\FFI::cast('void *', -2), '_exit'))->cdata; // RTLD_DEFAULT
            foreach ([SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGABRT] as $sig) {
                $c->signal($sig, $exit);
            }
            $null = $c->open('/dev/null', 2); // O_RDWR
            foreach ([0, 1, 2] as $fd) {
                $c->dup2($null, $fd); // "The process has forked and you cannot use..."
            }
            \FFI::cdef('void *SCDynamicStoreCopyProxies(void *);',
                       '/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration')
                ->SCDynamicStoreCopyProxies(null);
            $c->_exit(0); // no PHP shutdown in this copy of the daemon
        }
        pcntl_waitpid($pid, $status);
        return !pcntl_wifexited($status) || pcntl_wexitstatus($status) !== 0;
    }

    public static function log(string $msg): void
    {
        $line = 'speedrunner[' . getmypid() . "] $msg\n";
        @self::libc()->write(self::$log, $line, strlen($line));
    }

    private static function now(): float
    {
        return hrtime(true) / 1e9;
    }

    // --- fds through libc -------------------------------------------------

    /** Wait until fd is readable; false on timeout. */
    private static function waitReadable(int $fd, ?float $deadline): bool
    {
        $c = self::libc();
        $p = $c->new('struct pollfd');
        while (true) {
            $p->fd = $fd;
            $p->events = self::POLLIN;
            $p->revents = 0;
            $ms = $deadline === null ? -1 : max(0, (int) (($deadline - self::now()) * 1000));
            $r = $c->poll(\FFI::addr($p), 1, $ms);
            if ($r > 0) {
                return true;
            }
            if ($r === 0) {
                return false;
            }
            if (self::errno() !== self::EINTR) {
                return false;
            }
        }
    }

    private static function readExact(int $fd, int $n, ?float $deadline = null): string
    {
        $c = self::libc();
        $out = '';
        if ($n === 0) {
            return '';
        }
        $buf = $c->new("char[$n]");
        while (strlen($out) < $n) {
            if (!self::waitReadable($fd, $deadline)) {
                throw new \RuntimeException('handshake timed out');
            }
            $r = $c->read($fd, $buf, $n - strlen($out));
            if ($r === 0) {
                throw new \RuntimeException('client closed the connection');
            }
            if ($r < 0) {
                if (in_array(self::errno(), [self::EINTR, self::EAGAIN], true)) {
                    continue;
                }
                throw new \RuntimeException('read failed');
            }
            $out .= \FFI::string($buf, $r);
        }
        return $out;
    }

    private static function writeAll(int $fd, string $data): void
    {
        $c = self::libc();
        while ($data !== '') {
            $r = $c->write($fd, $data, strlen($data));
            if ($r < 0) {
                if (self::errno() === self::EINTR) {
                    continue;
                }
                throw new \RuntimeException('write failed');
            }
            $data = substr($data, $r);
        }
    }

    private static function sendMsg(int $fd, string $kind, string $payload = ''): void
    {
        self::writeAll($fd, $kind . pack('V', strlen($payload)) . $payload);
    }

    /** recvmsg of up to $len bytes, with the fds attached: [data, fds]. */
    private static function recvWithFds(int $fd, int $len, int $maxFds): array
    {
        $c = self::libc();
        $buf = $c->new("char[$len]");
        $space = self::CMSG_HDR + (int) ceil(4 * $maxFds / self::CMSG_ALIGN) * self::CMSG_ALIGN; // CMSG_SPACE
        $ctl = $c->new("unsigned char[$space]");
        $iov = $c->new('struct iovec');
        $iov->iov_base = \FFI::cast('void *', \FFI::addr($buf));
        $iov->iov_len = $len;
        $msg = $c->new('struct msghdr');
        $msg->msg_iov = \FFI::addr($iov);
        $msg->msg_iovlen = 1;
        $msg->msg_control = \FFI::cast('void *', \FFI::addr($ctl));
        $msg->msg_controllen = $space;
        do {
            $r = $c->recvmsg($fd, \FFI::addr($msg), self::MSG_CMSG_CLOEXEC);
        } while ($r < 0 && self::errno() === self::EINTR);
        if ($r < 0) {
            throw new \RuntimeException('recvmsg failed');
        }
        $fds = [];
        if ($msg->msg_controllen >= self::CMSG_HDR) {
            $raw = \FFI::string($ctl, (int) $msg->msg_controllen);
            $lenBytes = self::CMSG_HDR - 8;
            [, $cmsgLen] = unpack($lenBytes === 8 ? 'P' : 'V', substr($raw, 0, $lenBytes));
            [, $level, $type] = unpack('l2', substr($raw, $lenBytes, 8));
            if ($level === self::SOL_SOCKET && $type === self::SCM_RIGHTS) {
                $fds = array_values(unpack('l*', substr($raw, self::CMSG_HDR, $cmsgLen - self::CMSG_HDR)));
                if (self::MACOS) { // no MSG_CMSG_CLOEXEC
                    foreach ($fds as $received) {
                        $c->fcntl($received, self::F_SETFD, self::FD_CLOEXEC);
                    }
                }
            }
        }
        return [\FFI::string($buf, $r), $fds];
    }

    private static function sendWithFd(int $sock, string $data, int $passFd): void
    {
        $c = self::libc();
        $ctl = $c->new('unsigned char[' . (self::CMSG_HDR + 8) . ']');
        $len = self::CMSG_HDR + 4; // CMSG_LEN(4)
        $raw = (self::MACOS ? pack('V', $len) : pack('P', $len)) . pack('l', self::SOL_SOCKET)
            . pack('l', self::SCM_RIGHTS) . pack('l', $passFd);
        \FFI::memcpy($ctl, $raw, strlen($raw));
        $buf = $c->new('char[' . strlen($data) . ']');
        \FFI::memcpy($buf, $data, strlen($data));
        $iov = $c->new('struct iovec');
        $iov->iov_base = \FFI::cast('void *', \FFI::addr($buf));
        $iov->iov_len = strlen($data);
        $msg = $c->new('struct msghdr');
        $msg->msg_iov = \FFI::addr($iov);
        $msg->msg_iovlen = 1;
        $msg->msg_control = \FFI::cast('void *', \FFI::addr($ctl));
        $msg->msg_controllen = self::CMSG_HDR + 8; // CMSG_SPACE(4)
        if ($c->sendmsg($sock, \FFI::addr($msg), self::MSG_NOSIGNAL) < 0) {
            throw new \RuntimeException('sendmsg failed');
        }
    }

    // --- wire format ------------------------------------------------------

    /** [kind, body, fds] of a client request. */
    private static function recvRequest(int $conn, float $deadline): array
    {
        if (!self::waitReadable($conn, $deadline)) {
            throw new \RuntimeException('handshake timed out');
        }
        [$data, $fds] = self::recvWithFds($conn, 8, 5);
        if ($data === '') {
            throw new \UnderflowException('client closed the connection');
        }
        $data .= self::readExact($conn, 8 - strlen($data), $deadline);
        if (substr($data, 0, 3) !== self::MAGIC) {
            throw new \RuntimeException('bad magic');
        }
        [, $len] = unpack('V', substr($data, 4, 4));
        return [$data[3], self::readExact($conn, $len, $deadline), $fds];
    }

    private static function parseRequest(string $body): array
    {
        $pos = 0;
        $take = function (int $n) use ($body, &$pos): string {
            if ($pos + $n > strlen($body)) {
                throw new \RuntimeException('truncated request');
            }
            $v = substr($body, $pos, $n);
            $pos += $n;
            return $v;
        };
        $u32 = fn () => unpack('V', $take(4))[1];
        $blobs = function () use ($take, $u32): array {
            $out = [];
            for ($i = $u32(); $i > 0; $i--) {
                $out[] = $take($u32());
            }
            return $out;
        };
        $argv = $blobs();
        $env = [];
        foreach ($blobs() as $entry) {
            $eq = strpos($entry, '=');
            if ($eq !== false) {
                $env[substr($entry, 0, $eq)] = substr($entry, $eq + 1);
            }
        }
        $umask = $u32();
        $ignored = unpack('P', $take(8))[1];
        $groups = [];
        for ($i = $u32(); $i > 0; $i--) {
            $groups[] = $u32();
        }
        return ['argv' => $argv, 'env' => $env, 'umask' => $umask, 'ignored' => $ignored, 'groups' => $groups];
    }

    // --- state --------------------------------------------------------------

    private static function fingerprintPaths(string $script): array
    {
        $paths = [$script, PHP_BINARY];
        if (php_ini_loaded_file()) {
            $paths[] = php_ini_loaded_file();
        }
        foreach (explode(',', (string) php_ini_scanned_files()) as $f) {
            if (trim($f) !== '') {
                $paths[] = trim($f);
            }
        }
        return $paths;
    }

    private static function fingerprint(array $paths): array
    {
        clearstatcache();
        return array_map(function ($p) {
            $st = @stat($p);
            return $st ? [$st['mtime'], $st['size'], $st['ino'], $st['dev']] : null;
        }, $paths);
    }

    private static function stale(): bool
    {
        $s = self::$state;
        return self::fingerprint($s['fp_paths']) !== $s['fp'];
    }

    public static function statusText(): string
    {
        $s = self::$state;
        if ($s['disabled']) {
            return 'disabled: ' . $s['disabled'];
        }
        return 'ready pid=' . self::$parentPid . ' preload_ms=0 modules=0 levels=opcache '
            . 'learned=0 blacklisted=0 memo=0 gen=0';
    }

    // --- child: one invocation ----------------------------------------------

    private static function whyNotFast(array $req, int $peerGid, int $cwdFd, ?array $peerGroups = null): ?string
    {
        $s = self::$state;
        if ($s['disabled']) {
            return $s['disabled'];
        }
        // The script as typed, relative to the client's cwd (`php artisan`).
        $typed = $req['argv'][0] ?? '';
        $path = str_starts_with($typed, '/') ? $typed : self::dirPath($cwdFd) . "/$typed";
        if ($typed === '' || realpath($path) !== $s['script_real']) {
            return 'script path mismatch';
        }
        if (self::stale()) {
            return 'stale: installed files changed';
        }
        $groups = array_unique($peerGroups ?? $req['groups']);
        $mine = array_unique(self::ownGroups());
        sort($groups);
        sort($mine);
        if ($peerGid !== posix_getgid() || $groups !== $mine) {
            return "credentials differ from the daemon's";
        }
        foreach (self::STARTUP_ENV_KEYS as $key) {
            if (($req['env'][$key] ?? null) !== ($s['start_env'][$key] ?? null)) {
                return "startup env differs: $key";
            }
        }
        return null;
    }

    private static function applyRequest(array $req, array $fds): void
    {
        $c = self::libc();
        fflush(STDOUT);
        fflush(STDERR);
        foreach ([0, 1, 2] as $i) {
            $c->dup2($fds[$i], $i);
        }
        $c->fchdir($fds[3]);
        foreach ($fds as $fd) {
            $c->close($fd); // the tty too: PHP cannot reroute opens of /dev/tty
        }
        umask($req['umask']);

        // The C environment, for getenv() and subprocesses, then the
        // superglobals as the interpreter builds them (variables_order).
        foreach (array_keys(getenv()) as $key) {
            if (!array_key_exists($key, $req['env'])) {
                putenv((string) $key);
            }
        }
        foreach ($req['env'] as $key => $value) {
            putenv("$key=$value");
        }
        $argv = $req['argv'];
        $server = [];
        if (str_contains((string) ini_get('variables_order'), 'S')) {
            $server = $req['env'];
        }
        $script = $argv[0];
        $server += ['PHP_SELF' => $script, 'SCRIPT_NAME' => $script, 'SCRIPT_FILENAME' => $script,
                    'PATH_TRANSLATED' => $script, 'DOCUMENT_ROOT' => '',
                    'REQUEST_TIME_FLOAT' => microtime(true), 'REQUEST_TIME' => time(),
                    'argv' => $argv, 'argc' => count($argv)];
        $_SERVER = $server;
        $_ENV = str_contains((string) ini_get('variables_order'), 'E') ? $req['env'] : [];
        $GLOBALS['argv'] = $argv;
        $GLOBALS['argc'] = count($argv);

        // Dispositions a native interpreter starts with, plus the ignored ones
        // the client inherited.
        pcntl_async_signals(false);
        foreach ([SIGCHLD, SIGTERM, SIGUSR1, SIGINT, SIGHUP, SIGQUIT] as $sig) {
            pcntl_signal($sig, SIG_DFL);
        }
        // Ignored below PHP, as an inherited SIG_IGN is: pcntl's own table
        // still says default, natively too.
        for ($n = 1; $n < 32; $n++) {
            if ((($req['ignored'] >> $n) & 1) && $n !== SIGKILL && $n !== SIGSTOP) {
                self::libc()->signal($n, 1);
            }
        }
        pcntl_sigprocmask(SIG_SETMASK, []);
        set_exception_handler([self::class, 'uncaught']);
        self::$run = self::$state['script'];
    }

    /**
     * An uncaught exception, printed as PHP prints it (log and display,
     * as configured), without this file's frame in the traces.
     */
    public static function uncaught(\Throwable $e): void
    {
        $text = '';
        $n = 0;
        foreach (explode("\n", (string) $e) as $line) {
            if ($line === 'Stack trace:') {
                $n = 0;
            } elseif (preg_match('/^#\d+ (.*)$/', $line, $m)) {
                if (str_starts_with($m[1], __FILE__ . '(')) {
                    continue;
                }
                $line = '#' . $n++ . ' ' . $m[1];
            }
            $text .= ($text === '' ? '' : "\n") . $line;
        }
        $msg = "Uncaught $text\n  thrown in {$e->getFile()} on line {$e->getLine()}";
        if (filter_var(ini_get('log_errors'), FILTER_VALIDATE_BOOLEAN)) {
            error_log("PHP Fatal error:  $msg");
        }
        $display = strtolower((string) ini_get('display_errors'));
        if ($display === 'stderr') {
            fwrite(STDERR, "Fatal error: $msg\n");
        } elseif (in_array($display, ['1', 'on', 'yes', 'true', 'stdout'], true)) {
            echo "\nFatal error: $msg\n";
        }
        // Natively 255; after a user handler PHP would exit with 0. The
        // shutdown functions still run, as natively.
        exit(255);
    }

    private static function serveChild(int $conn): void
    {
        $c = self::libc();
        $deadline = self::now() + self::HANDSHAKE_TIMEOUT_S;
        try {
            [$peerUid, $peerGid, $peerGroups] = self::peerCreds($conn);
            if ($peerUid !== posix_getuid()) {
                exit(0);
            }
            try {
                [$kind, $body, $fds] = self::recvRequest($conn, $deadline);
            } catch (\UnderflowException $e) {
                exit(0); // a client that only checked we are up
            }
            if ($kind === 'P') {
                if (self::stale()) {
                    self::markExiting();
                    self::unlinkSocket();
                    posix_kill(self::$parentPid, SIGUSR1);
                    self::sendMsg($conn, 'P', 'stale: installed files changed');
                } else {
                    self::sendMsg($conn, 'P', self::statusText());
                }
                exit(0);
            }
            if ($kind === 'S') {
                self::sendMsg($conn, 'P', 'stopping');
                posix_kill(self::$parentPid, SIGTERM);
                exit(0);
            }
            $req = self::parseRequest($body);
            if (count($fds) < 4 || count($fds) > 5) {
                self::sendMsg($conn, 'N', 'missing file descriptors');
                exit(0);
            }
            $reason = self::whyNotFast($req, $peerGid, $fds[3], $peerGroups);
            if ($reason !== null) {
                if (str_starts_with($reason, 'stale')) {
                    self::markExiting();
                    self::unlinkSocket();
                    posix_kill(self::$parentPid, SIGUSR1);
                }
                self::sendMsg($conn, 'N', $reason);
                exit(0);
            }
            self::sendMsg($conn, 'R', pack('l', getmypid()));
            if (self::readExact($conn, 1, $deadline) !== 'G') {
                exit(0);
            }
            self::applyRequest($req, $fds);
        } catch (\Throwable $e) {
            self::log('child setup failed: ' . $e->getMessage());
            exit(0); // nothing has run yet: let the client fall back
        }
        $c->close(self::$log);
        // The connection stays open, close-on-exec, until we exit: the parent
        // sends the wait status on its own copy.
    }

    // --- parent ---------------------------------------------------------------

    private static function markExiting(): void
    {
        @file_put_contents(basename(self::$state['sock_path']) . '.lock', 'exiting ' . self::$parentPid . "\n");
    }

    private static function unlinkSocket(): void
    {
        $name = basename(self::$state['sock_path']);
        clearstatcache();
        $st = @stat($name);
        if ($st && $st['ino'] === self::$state['bound_ino']) {
            @unlink($name);
        }
    }

    private static function openRuntimeDir(string $path): void
    {
        $st = @lstat($path);
        if (!$st || ($st['mode'] & 0o170000) !== 0o040000 || $st['uid'] !== posix_getuid()
            || ($st['mode'] & 0o7777) !== 0o700) {
            throw new \RuntimeException("$path must be a directory owned by uid " . posix_getuid() . ' with mode 0700');
        }
        chdir($path);
        clearstatcache();
        $here = stat('.');
        if ($here['ino'] !== $st['ino'] || $here['dev'] !== $st['dev']) {
            throw new \RuntimeException("$path changed under us");
        }
    }

    /** The startup lock: the fd the client hands down, or our own. */
    private static function acquireLock(?int $lockFd, string $sockPath): ?int
    {
        $c = self::libc();
        if ($lockFd !== null) {
            $c->fcntl($lockFd, self::F_SETFD, self::FD_CLOEXEC);
            $c->flock($lockFd, self::LOCK_EX | self::LOCK_NB); // ours already: a no-op
            return $lockFd;
        }
        $f = fopen("$sockPath.lock", 'c+e');
        $deadline = self::now() + self::LOCK_WAIT_S;
        while (!flock($f, LOCK_EX | LOCK_NB)) {
            if (self::now() > $deadline) {
                return null;
            }
            usleep(50000);
        }
        self::$state['lock_stream'] = $f; // kept open: the lock lasts as long
        return -1;
    }

    private static function readCtl(int $fd): array
    {
        [$data, $fds] = self::recvWithFds($fd, 5, 1);
        if ($data === '') {
            return [null, '', $fds];
        }
        $data .= self::readExact($fd, 5 - strlen($data));
        [, $len] = unpack('V', substr($data, 1, 4));
        return [$data[0], self::readExact($fd, $len), $fds];
    }

    /** A disabled daemon answers without forking; returns a stop reason or null. */
    private static function handleInline(int $lsock): ?string
    {
        $c = self::libc();
        $conn = self::accept($lsock);
        if ($conn < 0) {
            return null;
        }
        try {
            [$kind, , $fds] = self::recvRequest($conn, self::now() + 0.5);
            foreach ($fds as $fd) {
                $c->close($fd);
            }
            if ($kind === 'S') {
                self::sendMsg($conn, 'P', 'stopping');
                return 'stop request';
            }
            self::sendMsg($conn, $kind === 'P' ? 'P' : 'N', $kind === 'P' ? self::statusText() : self::$state['disabled']);
        } catch (\Throwable $e) {
            // a client that gave up
        } finally {
            $c->close($conn);
        }
        return null;
    }

    /** Runs in a forked spare: wait for one client, then serve it. */
    private static function runSpare(int $lsock, int $ctl, array $inherited): void
    {
        $c = self::libc();
        foreach ([SIGCHLD, SIGTERM, SIGUSR1] as $sig) {
            pcntl_signal($sig, SIG_DFL);
        }
        foreach ($inherited as $fd) {
            $c->close($fd);
        }
        if (isset(self::$state['lock_stream'])) {
            fclose(self::$state['lock_stream']);
        }
        posix_setpgid(0, 0);
        $pfds = $c->new('struct pollfd[2]');
        while (true) {
            $pfds[0]->fd = $lsock;
            $pfds[0]->events = self::POLLIN;
            $pfds[0]->revents = 0;
            $pfds[1]->fd = $ctl;
            $pfds[1]->events = self::POLLIN;
            $pfds[1]->revents = 0;
            if ($c->poll($pfds, 2, -1) < 0) {
                continue;
            }
            if ($pfds[1]->revents !== 0) {
                exit(0); // retired, or the daemon is gone
            }
            $conn = self::accept($lsock);
            if ($conn >= 0) {
                break; // else another spare took it
            }
        }
        $c->close($lsock);
        // The parent keeps a copy, to send the wait status and replace us.
        // Its K says it has it: only that wait status reports our end, so
        // a daemon killed just now (still alive to the kernel for a few ms)
        // must not leave us serving. At end of file, the call has not
        // started: closing it sends the client to a native run.
        self::sendWithFd($ctl, 'A' . pack('V', 0), $conn);
        while (true) {
            try {
                [$kind, , $fds] = self::readCtl($ctl);
            } catch (\Throwable $e) {
                [$kind, $fds] = [null, []];
            }
            foreach ($fds as $fd) {
                $c->close($fd);
            }
            if ($kind === 'K') {
                break;
            }
            if ($kind === null) {
                $c->close($conn);
                exit(0);
            }
        }
        self::serveChild($conn);
    }

    /**
     * The daemon's own arguments. Run with register_argc_argv=0, so that
     * getopt(), which reads the interpreter's internal copy of $_SERVER
     * before $argv, sees the client's arguments: read them from /proc.
     */
    public static function ownArgv(): array
    {
        $words = self::cmdline();
        foreach ($words as $i => $word) {
            if (@realpath($word) === __FILE__) {
                return array_slice($words, $i);
            }
        }
        return [__FILE__];
    }

    /** Our command line: /proc, or on macOS sysctl KERN_PROCARGS2 (argc,
     *  the executable path and padding, then argv). */
    private static function cmdline(): array
    {
        if (!self::MACOS) {
            return explode("\0", rtrim((string) file_get_contents('/proc/self/cmdline'), "\0"));
        }
        $ffi = \FFI::cdef('int sysctl(int *, unsigned int, void *, size_t *, void *, size_t);',
                          '/usr/lib/libSystem.B.dylib');
        $mib = $ffi->new('int[3]');
        [$mib[0], $mib[1], $mib[2]] = [1, 49, getmypid()]; // CTL_KERN, KERN_PROCARGS2
        $size = $ffi->new('size_t');
        if ($ffi->sysctl($mib, 3, null, \FFI::addr($size), null, 0) !== 0) {
            return [];
        }
        $buf = $ffi->new("char[{$size->cdata}]");
        if ($ffi->sysctl($mib, 3, $buf, \FFI::addr($size), null, 0) !== 0) {
            return [];
        }
        $data = \FFI::string($buf, $size->cdata);
        $argc = unpack('l', $data)[1];
        $rest = substr($data, 4);
        $rest = ltrim(substr($rest, (int) strpos($rest, "\0")), "\0");
        return array_slice(explode("\0", $rest), 0, $argc);
    }

    public static function main(array $argv): void
    {
        $opts = [];
        for ($i = 1; $i + 1 < count($argv); $i += 2) {
            if (!in_array($argv[$i], ['--tool', '--script', '--socket', '--lock-fd', '--origin', '--tty'], true)) {
                fwrite(STDERR, "speedrunner_daemon: unknown argument {$argv[$i]}\n");
                exit(2);
            }
            $opts[substr($argv[$i], 2)] = $argv[$i + 1];
        }
        foreach (['tool', 'script', 'socket'] as $k) {
            if (!isset($opts[$k])) {
                fwrite(STDERR, "speedrunner_daemon: missing $k\n");
                exit(2);
            }
        }
        $c = self::libc();
        self::$parentPid = getmypid();
        self::$log = $c->dup(2);
        $c->fcntl(self::$log, self::F_SETFD, self::FD_CLOEXEC);
        $script = $opts['script'];
        self::$state = ['tool' => $opts['tool'], 'script' => $script, 'script_real' => realpath($script),
                        'sock_path' => $opts['socket'], 'disabled' => null, 'start_env' => getenv(),
                        'bound_ino' => null];
        if (ini_get('register_argc_argv')) {
            self::$state['disabled'] = 'getopt() would read the daemon\'s arguments: needs -d register_argc_argv=0';
        } elseif (self::cfUnsafe()) {
            self::$state['disabled'] = 'CoreFoundation used at startup: a forked child would be killed';
        }
        self::$state['fp_paths'] = self::fingerprintPaths($script);
        self::$state['fp'] = self::fingerprint(self::$state['fp_paths']);
        $opcache = function_exists('opcache_get_status') && (opcache_get_status(false)['opcache_enabled'] ?? false);
        if (!$opcache) {
            self::log('warning: OPcache is off; only the interpreter start is saved');
        }

        $dir = dirname($opts['socket']);
        self::openRuntimeDir($dir);
        $lockFd = self::acquireLock(isset($opts['lock-fd']) ? (int) $opts['lock-fd'] : null, $opts['socket']);
        if ($lockFd === null) {
            self::log('another daemon holds the lock; exiting');
            exit(0);
        }
        $name = basename($opts['socket']);
        @unlink($name);
        $lsock = self::socketFlags($c->socket(self::AF_UNIX, self::SOCK_STREAM | self::SOCK_NONBLOCK | self::SOCK_CLOEXEC, 0), true);
        $addr = $c->new('struct sockaddr_un');
        $addr->sun_family = self::AF_UNIX;
        \FFI::memcpy($addr->sun_path, $name, strlen($name));
        $old = umask(0o077);
        if ($c->bind($lsock, \FFI::addr($addr), \FFI::sizeof($addr)) !== 0 || $c->listen($lsock, 128) !== 0) {
            self::log("cannot listen on $name");
            exit(1);
        }
        chmod($name, 0o600);
        umask($old);
        clearstatcache();
        self::$state['bound_ino'] = stat($name)['ino'];
        self::log("ready for {$opts['tool']}, started by " . ($opts['origin'] ?? 'hand') . '; ' . self::statusText());

        $stop = null;
        pcntl_async_signals(true);
        pcntl_signal(SIGCHLD, function () {
        });
        pcntl_signal(SIGTERM, function () use (&$stop) {
            $stop ??= 'SIGTERM';
        });
        pcntl_signal(SIGUSR1, function () use (&$stop) {
            $stop ??= 'stale fingerprint';
        });
        $parentFds = $lockFd >= 0 ? [$lockFd] : [];
        $children = []; // pid => ['ctl' => fd|null, 'conn' => fd|null]
        $spare = null;
        $spareFailures = 0;
        $idle = (float) (getenv('SPEEDRUNNER_IDLE_TIMEOUT') ?: 4 * 3600);
        $lastActivity = self::now();

        $exiting = false;
        while (true) {
            // Stopping: out of the way first, so that new clients start a
            // fresh daemon; then the runs in progress, whose end only our
            // wait status reports (PHP gives no exit code before it stops).
            if ($stop !== null && !$exiting) {
                $exiting = true;
                self::log("exiting: $stop");
                self::markExiting();
                self::unlinkSocket();
                // The lock only says who serves the socket: a new daemon may
                // start now, however long the runs we wait for last.
                if (isset(self::$state['lock_stream'])) {
                    fclose(self::$state['lock_stream']);
                    unset(self::$state['lock_stream']);
                } elseif ($lockFd >= 0) {
                    $c->close($lockFd);
                }
                self::$state['disabled'] ??= 'exiting';
                if ($spare !== null && $children[$spare]['ctl'] !== null) {
                    $c->close($children[$spare]['ctl']); // the spare sees the end, and leaves
                    $children[$spare]['ctl'] = null;
                }
            }
            if ($exiting && !$children) {
                break;
            }
            if ($spare === null && !self::$state['disabled']) {
                $sv = $c->new('int[2]');
                $c->socketpair(self::AF_UNIX, self::SOCK_STREAM | self::SOCK_CLOEXEC, 0, $sv);
                self::socketFlags($sv[0], false);
                self::socketFlags($sv[1], false);
                $pid = pcntl_fork();
                if ($pid === 0) {
                    $c->close($sv[0]);
                    $inherited = $parentFds;
                    foreach ($children as $ch) {
                        foreach (['ctl', 'conn'] as $k) {
                            if ($ch[$k] !== null) {
                                $inherited[] = $ch[$k];
                            }
                        }
                    }
                    self::runSpare($lsock, $sv[1], $inherited);
                    return; // ready: the top level includes the tool
                }
                $c->close($sv[1]);
                $children[$pid] = ['ctl' => $sv[0], 'conn' => null];
                $spare = $pid;
            }
            $watch = [];
            foreach ($children as $pid => $ch) {
                if ($ch['ctl'] !== null) {
                    $watch[$pid] = $ch['ctl'];
                }
            }
            if (self::$state['disabled']) {
                $watch['listen'] = $lsock; // answered here, without forking
            }
            $n = count($watch);
            $pfds = $c->new('struct pollfd[' . max(1, $n) . ']');
            $i = 0;
            foreach ($watch as $fd) {
                $pfds[$i]->fd = $fd;
                $pfds[$i]->events = self::POLLIN;
                $pfds[$i]->revents = 0;
                $i++;
            }
            $timeout = $exiting ? 0.05 : max(1.0, $lastActivity + $idle - self::now());
            // A child whose control socket closed is exiting, but may not have
            // been reapable at the last waitpid; its SIGCHLD then came while
            // PHP ran here, outside poll, and wakes nothing: look again soon.
            foreach ($children as $ch) {
                if ($ch['ctl'] === null) {
                    $timeout = 0.005;
                    break;
                }
            }
            $r = $c->poll($pfds, $n, (int) ($timeout * 1000));
            $i = 0;
            foreach ($watch as $pid => $fd) {
                if ($r > 0 && $pfds[$i]->revents !== 0 && $pid === 'listen') {
                    $stop ??= self::handleInline($lsock);
                } elseif ($r > 0 && $pfds[$i]->revents !== 0) {
                    [$kind, , $fds] = self::readCtl($fd);
                    if ($kind === 'A' && $fds) {
                        $children[$pid]['conn'] = array_shift($fds);
                        try {
                            self::sendMsg($fd, 'K'); // the child may start
                        } catch (\Throwable $e) {
                            // died meanwhile: reaped below
                        }
                        $lastActivity = self::now();
                        if ($pid === $spare) {
                            $spare = null;
                            $spareFailures = 0;
                        }
                    }
                    foreach ($fds as $extra) {
                        $c->close($extra);
                    }
                    if ($kind === null) {
                        $c->close($fd);
                        $children[$pid]['ctl'] = null;
                    }
                }
                $i++;
            }
            while (($pid = pcntl_waitpid(-1, $status, WNOHANG)) > 0) {
                if (!isset($children[$pid])) {
                    continue;
                }
                $ch = $children[$pid];
                unset($children[$pid]);
                if ($ch['ctl'] !== null) {
                    // What it sent before dying (its A): without waiting.
                    while (self::waitReadable($ch['ctl'], self::now())) {
                        [$kind, , $fds] = self::readCtl($ch['ctl']);
                        if ($kind === 'A' && $fds) {
                            $ch['conn'] = array_shift($fds);
                        }
                        foreach ($fds as $extra) {
                            $c->close($extra);
                        }
                        if ($kind === null) {
                            break;
                        }
                    }
                    $c->close($ch['ctl']);
                }
                if ($pid === $spare) {
                    $spare = null;
                    if (++$spareFailures >= 3) {
                        self::$state['disabled'] = 'spare children keep dying';
                        self::log(self::$state['disabled']);
                    }
                }
                if ($ch['conn'] !== null) {
                    $w = pcntl_wifsignaled($status) ? pack('Cl', 1, pcntl_wtermsig($status))
                                                    : pack('Cl', 0, pcntl_wexitstatus($status));
                    try {
                        self::sendMsg($ch['conn'], 'W', $w);
                    } catch (\Throwable $e) {
                        // client gone
                    }
                    $c->close($ch['conn']);
                }
            }
            $active = false;
            foreach ($children as $ch) {
                $active = $active || $ch['conn'] !== null;
            }
            if (!$active && self::now() >= $lastActivity + $idle) {
                $stop ??= 'idle timeout';
            }
        }
        exit(0); // the lock was let go of on the way out
    }
}

}

namespace {
    Speedrunner\Daemon::main(Speedrunner\Daemon::ownArgv());
    // In a child that a client told to go: run the tool here, at the top
    // level, as the interpreter would have run it.
    include Speedrunner\Daemon::$run;
}
