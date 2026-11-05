# speedrunner daemon for Ruby tools: keeps one tool preloaded and serves each
# invocation from a forked child. It speaks the protocol of
# speedrunner_daemon.py, to the same client; see there for the wire format and
# the model (prefork spare, control socketpair, X then W, rule n°1). Only
# what differs for Ruby is explained here.
#
# Started by the client, with the tool's own interpreter:
#
#   <ruby> speedrunner_daemon.rb --tool NAME --script PATH --socket PATH
#                              [--lock-fd N] [--origin TEXT]
#
# Standard library only. Linux and macOS: see the platform section.
# Preload levels (SPEEDRUNNER_PRELOAD):
#   1  the top-level requires of the script and, for a RubyGems binstub, of
#      the gem's executable once the gem is activated;
#   2  the gems and files earlier runs loaded, learned by the children and
#      vetted by a probe, as in speedrunner_daemon.py.
require "digest"
require "fiddle"
require "io/console"
require "json"
require "pty"
require "rbconfig"
require "socket"

module Speedrunner
  MAGIC = "HS1".b
  HANDSHAKE_TIMEOUT_S = 5.0
  LOCK_WAIT_S = 10.0
  IDLE_TIMEOUT_S = Float(ENV.fetch("SPEEDRUNNER_IDLE_TIMEOUT", 4 * 3600))
  # A tool that depends on the cwd it loaded in: how long its directory must
  # have gone unused before the daemon follows a client elsewhere.
  FOLLOW_AFTER_S = Float(ENV.fetch("SPEEDRUNNER_FOLLOW_AFTER", 30))
  # Read once at interpreter startup (RubyGems is loaded at boot): a child
  # cannot honour a client whose values differ from the daemon's.
  STARTUP_ENV_PREFIXES = %w[RUBY GEM_].freeze
  STARTUP_ENV_KEYS = %w[LC_ALL LC_CTYPE LANG].freeze
  IN_CLOEXEC = 0o2000000 # O_CLOEXEC: the same on x86 and arm64 (asm-generic)
  DAEMON_FILE = File.expand_path(__FILE__)
  PARENT_PID = Process.pid
  LOG = STDERR.dup
  CHILD = { conn: nil, files: nil, ctl: nil, learn: nil }
  PROBE_TIMEOUT_S = 30.0
  PROBE_STEP_S = 0.25 # a probe hands back what is left after this long
  MACOS = RUBY_PLATFORM.include?("darwin")
  # The client sets OBJC_DISABLE_INITIALIZE_FORK_SAFETY for us on macOS (see
  # objc_fork_env in client/src/platform.rs): ours, not the tool's.
  ENV.delete("OBJC_DISABLE_INITIALIZE_FORK_SAFETY") if ENV.delete("SPEEDRUNNER_OBJC_ADDED")

  def self.log(msg)
    LOG.write("speedrunner[#{Process.pid}] #{msg}\n")
  rescue IOError, SystemCallError
    nil
  end

  def self.now = Process.clock_gettime(Process::CLOCK_MONOTONIC)

  # --- platform ------------------------------------------------------------
  # Linux: /proc, inotify, SO_PEERCRED, O_TMPFILE. macOS: libproc, stat,
  # LOCAL_PEERCRED, an unlinked file, fchdir, and CoreFoundation (see
  # speedrunner_daemon.py).

  LIBC = Fiddle.dlopen(nil)

  # [uid, gid, groups] of the peer of a Unix socket; groups nil on Linux,
  # where the request's are compared with ours.
  def self.peer_creds(conn)
    if MACOS
      # struct xucred: u_int version; uid_t uid; short ngroups; gid_t groups[16].
      data = conn.getsockopt(0, Socket::LOCAL_PEERCRED).data
      uid, ngroups = data.unpack("x4Ls")
      groups = data.unpack("x12L#{ngroups.clamp(0, 16)}")
      return [uid, groups.first || -1, groups]
    end
    _pid, uid, gid = conn.getsockopt(Socket::SOL_SOCKET, Socket::SO_PEERCRED).data.unpack("i3")
    [uid, gid, nil]
  end

  # Our groups as the kernel has them: on macOS, Process.groups may be the
  # user's, resolved anew; ours are read as a peer's are, over a socketpair.
  def self.own_groups
    return Process.groups unless MACOS

    @own_groups ||= begin
      a, b = UNIXSocket.pair
      peer_creds(a)[2]
    ensure
      a&.close
      b&.close
    end
  end

  def self.native_thread_count
    return Dir.children("/proc/self/task").size unless MACOS

    info = "\0".b * 96 # struct proc_taskinfo, PROC_PIDTASKINFO; pti_threadnum
    @proc_pidinfo ||= Fiddle::Function.new(LIBC["proc_pidinfo"], [Fiddle::TYPE_INT, Fiddle::TYPE_INT,
                                           Fiddle::TYPE_LONG_LONG, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
                                           Fiddle::TYPE_INT)
    return -1 if @proc_pidinfo.call(Process.pid, 4, 0, info, 96) != 96 # unknown: threads

    info.unpack1("x84l")
  end

  # A file only we can reach, for output of any size.
  def self.anon_file
    dir = ENV.fetch("TMPDIR", "/tmp")
    return File.open(dir, File::RDWR | File::TMPFILE, 0o600) unless MACOS

    path = File.join(dir, ".speedrunner-#{Process.pid}-#{rand(1 << 32)}")
    File.open(path, File::RDWR | File::CREAT | File::EXCL | File::NOFOLLOW, 0o600).tap { File.unlink(path) }
  end

  # chdir to the directory open as io. Ruby 3.1 has no fchdir.
  def self.chdir_to(io)
    return Dir.chdir("/proc/self/fd/#{io.fileno}") unless MACOS

    fchdir = Fiddle::Function.new(LIBC["fchdir"], [Fiddle::TYPE_INT], Fiddle::TYPE_INT)
    raise SystemCallError.new("fchdir", Fiddle.last_error) unless fchdir.call(io.fileno).zero?
  end

  # macOS: whether a forked child of ours would be killed for using
  # CoreFoundation, because this process used it first: see cf_unsafe in
  # speedrunner_daemon.py. Here the canary calls SCDynamicStoreCopyProxies,
  # as _scproxy does, and leaves by _exit, not by a crash (crash report).
  def self.cf_unsafe?
    return false unless MACOS

    [STDOUT, STDERR].each(&:flush)
    pid = Process.fork do
      signal = Fiddle::Function.new(LIBC["signal"], [Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP], Fiddle::TYPE_VOIDP)
      %w[SEGV BUS ILL TRAP ABRT].each { |sig| signal.call(Signal.list.fetch(sig), LIBC["_exit"]) }
      null = File.open(File::NULL, "r+")
      [STDIN, STDOUT, STDERR].each { |io| io.reopen(null) }
      sc = Fiddle.dlopen("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration")
      Fiddle::Function.new(sc["SCDynamicStoreCopyProxies"], [Fiddle::TYPE_VOIDP], Fiddle::TYPE_VOIDP).call(nil)
      exit!(0)
    end
    Process.wait(pid)
    !$?.success?
  end

  # --- wire format ---------------------------------------------------------

  def self.send_msg(io, kind, payload = "".b)
    io.write(kind.b + [payload.bytesize].pack("V") + payload.b)
  end

  def self.read_exact(io, n, deadline = nil)
    buf = "".b
    while buf.bytesize < n
      if deadline
        left = deadline - now
        raise IOError, "handshake timed out" if left <= 0 || !IO.select([io], nil, nil, left)
      end
      buf << io.readpartial(n - buf.bytesize) # EOFError at end of file
    end
    buf
  end

  # Returns [kind, body, fds]; fds are IO objects, close-on-exec.
  def self.recv_request(conn, deadline)
    raise IOError, "handshake timed out" unless IO.select([conn], nil, nil, deadline - now)
    data, _addr, _flags, *controls = conn.recvmsg(8, 0, nil, scm_rights: true)
    fds = controls.flat_map { |c| c.unix_rights || [] }
    raise EOFError, "client closed the connection" if data.nil? || data.empty?
    data = data.b + read_exact(conn, 8 - data.bytesize, deadline)
    raise ArgumentError, "bad magic" unless data.byteslice(0, 3) == MAGIC
    [data.byteslice(3, 1), read_exact(conn, data.byteslice(4, 4).unpack1("V"), deadline), fds]
  end

  class Reader
    def initialize(data)
      @data = data
      @pos = 0
    end

    def take(n)
      v = @data.byteslice(@pos, n)
      raise ArgumentError, "truncated request" unless v && v.bytesize == n
      @pos += n
      v
    end

    def u32 = take(4).unpack1("V")
    def u64 = take(8).unpack1("Q<")
    def blob = take(u32)
    def blobs = Array.new(u32) { blob }
  end

  Request = Struct.new(:argv, :env, :umask, :ignored_signals, :groups)

  def self.parse_request(body)
    r = Reader.new(body)
    argv = r.blobs
    env = {}
    r.blobs.each do |entry|
      key, sep, value = entry.partition("=")
      env[key] = value unless sep.empty?
    end
    Request.new(argv, env, r.u32, r.u64, Array.new(r.u32) { r.u32 })
  end

  # --- daemon state --------------------------------------------------------

  class State
    attr_accessor :tool_traps, :daemon_traps, :tool, :script, :sock_path, :lock_fd, :origin, :disabled, :start_env,
                  :pre_import_env, :import_env_changed, :import_env_deleted, :import_env_read,
                  :start_cwd, :start_cwd_id, :runtime_dir, :pending_reads, :import_cwd_read,
                  :import_cwd_files, :import_dirs, :load_dirs,
                  :import_output, :fingerprint_paths, :fingerprint, :fingerprint_set, :watch,
                  :preload_s, :bound_ino, :levels, :gem_binstub, :learned, :learned_gems,
                  :blacklist, :pending, :pending_gems, :gen, :cache_path, :restart, :tty,
                  :import_tty_read, :skip, :learning, :gc_next

    def initialize(tool, script, sock_path)
      @tool, @script, @sock_path = tool, script, sock_path
      @origin = "hand"
      @start_env = ENV.to_h
      @pre_import_env = {}
      @import_env_changed = {}
      @import_env_deleted = []
      @import_env_read = []
      @import_cwd_read = false
      @import_cwd_files = {} # relative path read as it loaded -> [as it was, there]
      @import_dirs = [] # paths it only found to be directories
      @load_dirs = {} # path -> [made by Dir.mktmpdir?, mode]: see keep_load_dirs
      @import_output = ["".b, "".b]
      @fingerprint_paths = []
      @fingerprint = []
      @fingerprint_set = {}
      @preload_s = 0.0
      @gc_next = 0
      @levels = ENV.fetch("SPEEDRUNNER_PRELOAD", "1,2,3").split(",").map(&:strip) & %w[1 2]
      @learned = []      # level 2: files loaded because children did
      @learned_gems = [] # level 2: [name, version] activated because children did
      @blacklist = {}    # file -> why it is never preloaded
      @pending = {}      # ordered set: learned from children, not yet vetted
      @pending_gems = {}
      @gen = 0           # bumped whenever the preloaded state changes
      @tty = nil             # TtyStandIn: the starting client's terminals, during loads
      @tool_traps = {}       # what files trapped as they loaded, for the children
      @daemon_traps = {}     # the daemon's own, once it serves
      @import_tty_read = {}  # fd -> whether a terminal, as load-time code found it
      @skip = {}         # level 1: file -> why a fresh daemon must not load it
      @learning = nil    # what apply_learned still has to vet, across its steps
    end
  end

  def self.b(value) = value.to_s.b

  def self.startup_env(env)
    env.select { |k, _| STARTUP_ENV_PREFIXES.any? { |p| k.start_with?(p) } || STARTUP_ENV_KEYS.include?(k) }
       .to_h { |k, v| [b(k), b(v)] }
  end

  # The keys read through ENV while active, e.g. by top-level code of a
  # required file. Prepended for good; it only records while switched on.
  module EnvReads
    KEYS = []
    ON = [false]

    %i[[] fetch key? has_key? include? member?].each do |name|
      define_method(name) do |key, *rest, &blk|
        KEYS << key.to_s if ON[0]
        super(key, *rest, &blk)
      end
    end
  end
  ENV.singleton_class.prepend(EnvReads)

  # Whether ARGV was read while active. A file that reads it at load would
  # keep the daemon's (empty), so it is never preloaded.
  module ArgvReads
    ON = [false]
    READ = [false]

    (Array.public_instance_methods(false) + %i[dup clone] - %i[hash eql?]).each do |name|
      define_method(name) do |*args, &blk|
        READ[0] = true if ON[0]
        super(*args, &blk)
      end
    end
  end
  ARGV.singleton_class.prepend(ArgvReads)

  # Run the block; return its result and whether it read ARGV.
  def self.reads_argv
    ArgvReads::ON[0] = true
    ArgvReads::READ[0] = false
    [yield, ArgvReads::READ[0]]
  ensure
    ArgvReads::ON[0] = false
  end

  # What a file reads of the filesystem as it loads: it keeps what it found,
  # in the daemon, for every client. See FileReads in speedrunner_daemon.py.
  # Installed for the load only, so that no frame of ours is in a backtrace.
  # Not rubygems' own reads: its layout is in the fingerprint already.
  LOCK_AT_LOAD = "takes a file lock at load"
  LOCK_SEEN = [false] # also when the file swallowed what the wrapper raised

  class FileReads
    NOT_INPUTS = %r{\A/(proc|sys|dev)/}
    COMPILED = /\.(so|bundle|dylib)\z/
    RUBYGEMS = %r{/rubygems(/|\.rb)}
    # Its entries change all the time, and no tool's output depends on them.
    TEMP_ROOTS = ["/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp", ENV.fetch("TMPDIR", "/tmp").chomp("/")].freeze
    # Adapter: rspec keeps the cwd it loaded in; each child sets it again.
    CWD_NOT_KEPT = %r{/rspec/core/(filter_manager|backtrace_formatter)\.rb\z}
    READERS = { IO => %i[read binread readlines foreach],
                File => %i[read binread readlines foreach open new] }.freeze
    LISTERS = { Dir => %i[entries children each_child foreach empty?] }.freeze
    PROBES = { File => %i[exist? file? directory? size size? zero? empty? stat lstat mtime ctime atime
                          readable? symlink? readlink realpath],
               Dir => %i[exist?] }.freeze
    attr_reader :files, :listed, :probed, :relative, :cwd_read, :made

    def initialize(start_cwd, runtime_dir)
      @start_cwd = start_cwd
      @runtime_dir = runtime_dir
      @files = {}
      @listed = {}
      @probed = {}
      @relative = {}
      @cwd_read = false
      @made = {} # path -> [made by Dir.mktmpdir?, mode]: see keep_load_dirs
      @restore = []
    end

    def note(path, into)
      path = path.to_path if path.respond_to?(:to_path)
      return unless path.is_a?(String) && !path.match?(COMPILED)
      return if reader.match?(RUBYGEMS)
      return if into.equal?(@listed) && TEMP_ROOTS.include?(path.chomp("/"))

      if !path.start_with?("/")
        @relative[path] = true
      elsif !path.match?(NOT_INPUTS)
        into[path] = true
      end
    end

    def note_cwd
      from = reader
      @cwd_read = true unless from.match?(RUBYGEMS) || from.match?(CWD_NOT_KEPT)
    end

    # The file that made the call, past the wrapper's frames. Ours are no reads.
    def reader
      from = caller_locations(2, 8).find { |l| l.path != DAEMON_FILE || !l.label.match?(/\b(wrap|start)\b/) }
      from&.path == DAEMON_FILE ? "/rubygems/" : from&.path.to_s
    end

    def writes?(args, kwargs)
      mode = kwargs[:mode] || kwargs[:flags] || args[1]
      case mode
      when Integer then mode.anybits?(File::WRONLY | File::TRUNC | File::EXCL)
      when String then mode.start_with?("w", "a")
      else false
      end
    end

    def wrap(owner, name, on: nil, after: nil, &note)
      singleton = on || (owner.equal?(Kernel) ? Kernel : owner.singleton_class)
      own = singleton.instance_methods(false).include?(name) || singleton.private_instance_methods(false).include?(name)
      original = singleton.instance_method(name)
      visibility = singleton.private_method_defined?(name) ? :private : :public
      singleton.send(:define_method, name) do |*args, **kwargs, &blk|
        note&.call(args, kwargs)
        bound = original.bind(self)
        result = kwargs.empty? ? bound.call(*args, &blk) : bound.call(*args, **kwargs, &blk)
        after&.call(args)
        result
      end
      singleton.send(visibility, name)
      @restore << lambda do
        singleton.send(:remove_method, name)
        if own
          singleton.send(:define_method, name, original)
          singleton.send(visibility, name)
        end
      end
    end

    # Raising stops the call: the daemon never holds it, which would refuse
    # every other run, and every child would share it.
    def refuse_locks
      wrap(File, :flock, on: File) do |args, _|
        next if args[0].to_i.anybits?(File::LOCK_UN) || reader.match?(RUBYGEMS)

        LOCK_SEEN[0] = true
        raise Refused, LOCK_AT_LOAD
      end
    end

    # After the call: one that was already there raises, and is not made here.
    # Dir.mkdir is what Dir.mktmpdir and FileUtils.mkdir_p come down to, and it
    # is there before either is required. Relative: where each run would make it.
    def note_mkdirs
      wrap(Dir, :mkdir, after: lambda do |args|
        path = args[0].respond_to?(:to_path) ? args[0].to_path : args[0]
        from = reader
        next if !path.is_a?(String) || !path.start_with?("/") || from.match?(RUBYGEMS)

        temp = from.end_with?("/tmpdir.rb")
        @made[File.absolute_path(path)] = [temp, temp ? 0o700 : (args[1] || 0o777)]
      end)
    end

    # In a probe, which leaves without running at_exit handlers. A name of the
    # tool's own may be in use by now: only if nothing was put in it.
    def remove_made
      @made.to_a.reverse_each do |path, (temp, _)|
        temp ? Speedrunner.remove_tree(path) : Dir.rmdir(path)
      rescue SystemCallError
        nil
      end
    end

    def start
      refuse_locks
      note_mkdirs
      READERS.each do |owner, names|
        names.each do |name|
          wrap(owner, name) { |args, kwargs| note(args[0], @files) unless writes?(args, kwargs) }
        end
      end
      wrap(Kernel, :open) { |args, kwargs| note(args[0], @files) unless writes?(args, kwargs) }
      LISTERS.each do |owner, names|
        names.each { |name| wrap(owner, name) { |args, _| note(args[0], @listed) } }
      end
      PROBES.each do |owner, names|
        names.each { |name| wrap(owner, name) { |args, _| note(args[0], @probed) } }
      end
      %i[pwd getwd].each { |name| wrap(Dir, name) { note_cwd } }
      wrap(File, :expand_path) { |args, _| note_cwd if !args[1] && !args[0].to_s.start_with?("/", "~") }
      %i[glob []].each do |name|
        wrap(Dir, name) do |args, kwargs|
          pattern = Array(args[0]).first.to_s
          if kwargs[:base] then note(kwargs[:base], @listed)
          elsif pattern.start_with?("/") then note(File.dirname(pattern[/\A[^*?\[{]*/] + "x"), @listed)
          else note_cwd
          end
        end
      end
      Dir.chdir(@start_cwd) if @start_cwd
    rescue SystemCallError
      @start_cwd = nil
    end

    def finish
      Speedrunner.chdir_to(@runtime_dir) if @start_cwd
      @restore.reverse_each(&:call)
    end
  end

  def self.reading_files(state)
    reads = FileReads.new(state.start_cwd, state.runtime_dir)
    reads.start
    begin
      yield
    ensure
      reads.finish
      state.pending_reads = reads
    end
  end

  # Cover what the load saw, as it is now. The cache's name does not depend
  # on them: a config file that changed keeps what the daemon has learned.
  def self.keep_file_reads(state)
    reads = state.pending_reads
    return unless reads

    state.pending_reads = nil
    reads.made.each { |path, how| state.load_dirs[path] = how if File.directory?(path) }
    # Runs write in a temporary directory: nothing to compare. One of the tool's
    # own may hold its config, and only its being there is the tool's doing.
    temp = state.load_dirs.select { |_, (made_temp, _)| made_temp }
    [reads.files, reads.listed, reads.probed].each do |found|
      found.delete_if { |path, _| in_load_dir?(temp, path) }
    end
    reads.probed.delete_if { |path, _| state.load_dirs.key?(path) }
    state.import_cwd_read ||= reads.cwd_read
    if state.start_cwd
      reads.relative.each_key do |path|
        state.import_cwd_files[path] ||= [cwd_file_key(File.join(state.start_cwd, path))]
      end
    end
    candidates = reads.files.keys | reads.probed.keys
    dirs = candidates.select { |p| File.directory?(p) }
    extend_fingerprint(state, reads.listed.keys) # by stat: a watch would miss new entries
    extend_fingerprint(state, candidates - dirs, watch: true)
    state.import_dirs |= dirs & reads.probed.keys
    extend_fingerprint(state, dirs - reads.probed.keys)
  end

  def self.cwd_file_key(path)
    File.directory?(path) ? "dir" : stat_key(path)
  end

  def self.in_load_dir?(dirs, path)
    dirs.key?(path) || dirs.any? { |dir, _| path.start_with?("#{dir}/") }
  end

  def self.remove_tree(path)
    Dir.children(path).each do |entry|
      file = File.join(path, entry)
      File.lstat(file).directory? ? remove_tree(file) : File.unlink(file)
    end
    Dir.rmdir(path)
  end

  # A directory made as files loaded: natively each process has its own; here
  # its path is kept in the tool's state, so all children share it, and the
  # first to leave would take it away from the others. Each child makes it again
  # if it is gone (a temp cleaner), and leaves its removal to the daemon, whose
  # at_exit handlers run as it exits.
  #
  # At the level of File and Dir, under every name they go by: that is what
  # FileUtils, Pathname and a tool's own loop come down to. An at_exit handler
  # registered as files loaded runs after the one run_tool adds, in the phase
  # CHILD[:exiting] marks: it removes nothing in such a directory. A command may
  # (`tool cache clear`, in a directory of the tool's own), except for the
  # directory made by Dir.mktmpdir itself, which others may be using. What
  # another process removes is out of reach.
  def self.keep_load_dirs(state)
    return if state.load_dirs.empty?

    state.load_dirs.each do |path, (_, mode)|
      next if File.directory?(path)

      FileUtils_mkdir_p(File.dirname(path))
      Dir.mkdir(path, mode)
    rescue SystemCallError
      nil
    end
    CHILD[:load_dirs] = state.load_dirs
    File.singleton_class.prepend(Module.new do
      %i[unlink delete].each do |name|
        define_method(name) do |*paths|
          kept, others = paths.partition { |path| Speedrunner.spared?(path, false) }
          super(*others) + kept.size
        end
      end
    end)
    Dir.singleton_class.prepend(Module.new do
      %i[rmdir unlink delete].each do |name|
        define_method(name) { |path| Speedrunner.spared?(path, true) ? 0 : super(path) }
      end
    end)
  end

  def self.spared?(path, a_directory)
    path = path.to_path if path.respond_to?(:to_path)
    return false unless path.is_a?(String)

    path = File.absolute_path(path)
    dirs = CHILD[:load_dirs]
    return true if a_directory && dirs[path]&.first
    CHILD[:exiting] && in_load_dir?(dirs, path)
  end

  # Whether what a file read through the cwd as it loaded would read otherwise
  # in this client's. A change where the daemon started is a stale daemon. A
  # client elsewhere runs natively; if that directory has gone unused for a
  # while, the daemon restarts, and so follows it. The socket's mtime says when
  # it was last used: a call in a loop across directories moves nothing. Runs
  # in the child, which exits if it falls back.
  def self.why_cwd_differs(state, cwd_io)
    return nil unless state.import_cwd_read || state.import_cwd_files.any?

    st = cwd_io.stat
    same = state.start_cwd_id == [st.dev, st.ino]
    socket = File.basename(state.sock_path)
    unused = begin
      Time.now - File.mtime(socket) >= FOLLOW_AFTER_S # before the chdir: our cwd is its directory
    rescue SystemCallError
      false
    end
    why = "import-time cwd differs" if state.import_cwd_read && !same
    unless why
      runtime = Dir.pwd
      chdir_to(cwd_io)
      state.import_cwd_files.each do |path, (key)|
        next if cwd_file_key(File.join(Dir.pwd, path)) == key
        return "stale: #{path}, read at load, changed" if same

        why = "import-time file differs: #{path}"
        break
      end
      Dir.chdir(runtime)
    end
    if why
      return unused ? "stale: its directory unused, and #{why}" : why
    end
    begin
      File.utime(nil, nil, socket) if same
    rescue SystemCallError
      nil
    end
    nil
  end

  def self.top_level_requires(path)
    File.foreach(path).filter_map do |line|
      next unless line =~ /\A(require|require_relative)\s*\(?\s*["']([^"'#]+)["']\s*\)?\s*(#.*)?\Z/
      $1 == "require" ? $2 : File.expand_path($2, File.dirname(path))
    end
  rescue SystemCallError
    []
  end

  # Run the block with fds 1 and 2 captured; return their bytes. At fd
  # level, so that IO objects created meanwhile stay the real ones. On the
  # fds a TtyStandIn covers, a pty stands in, so that code checking for a
  # terminal finds what the client that started us has.
  def self.capture_fds(tty = nil)
    [STDOUT, STDERR].each(&:flush)
    [1, 2].each { |fd| settle_pty(tty.master[fd], tty.slave[fd]) if tty&.covers?(fd) }
    syncs = [STDOUT.sync, STDERR.sync]
    saved = [STDIN.dup, STDOUT.dup, STDERR.dup]
    sinks = [1, 2].map do |fd|
      if tty&.covers?(fd)
        tty.master[fd]
      else
        anon_file
      end
    end
    drained = { sinks[0] => "".b, sinks[1] => "".b }
    begin
      STDIN.reopen(tty.slave[0]) if tty&.covers?(0)
      STDOUT.reopen(tty&.covers?(1) ? tty.slave[1] : sinks[0])
      STDERR.reopen(tty&.covers?(2) ? tty.slave[2] : sinks[1])
      draining_ptys(sinks.grep_v(File), drained) do
        yield
      ensure # what is buffered may fill a pty too
        [STDOUT, STDERR].each { |io| io.flush rescue nil }
        [1, 2].each do |fd|
          next unless tty&.covers?(fd)
          begin
            tty.slave[fd].syswrite(PTY_MARK)
          rescue Errno::EINTR # the drain's timer
            retry
          end
        end
      end
    ensure
      STDIN.reopen(saved[0])
      STDOUT.reopen(saved[1])
      STDERR.reopen(saved[2])
      STDOUT.sync, STDERR.sync = syncs
    end
    sinks.map { |f| f.is_a?(File) ? (f.rewind; f.read.b) : read_to_mark(f, drained[f]) }
  ensure
    (saved || []).each(&:close)
    (sinks || []).each { |f| f.close if f.is_a?(File) }
  end

  # A file that prints as it loads writes to a pty stand-in, which blocks
  # once full, with nobody reading: read its master on a timer meanwhile,
  # which interrupts the write. No thread: the daemon must fork. Not over a
  # tool's own use of the timer.
  DRAIN_INTERVAL_US = 10_000

  def self.draining_ptys(masters, drained)
    return yield if masters.empty?
    previous = trap("ALRM") do
      masters.each do |master|
        loop do
          chunk = master.read_nonblock(65_536, exception: false)
          break if chunk.nil? || chunk == :wait_readable
          drained[master] << chunk
        end
      rescue Errno::EIO
        nil
      end
    end
    unless ["DEFAULT", "SYSTEM_DEFAULT", nil].include?(previous)
      trap("ALRM", previous)
      return yield
    end
    set_timer(DRAIN_INTERVAL_US)
    begin
      yield
    ensure
      set_timer(0)
      trap("ALRM", "DEFAULT")
    end
  end

  def self.set_timer(microseconds)
    @setitimer ||= Fiddle::Function.new(LIBC["setitimer"], [Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP, Fiddle::TYPE_VOIDP],
                                        Fiddle::TYPE_INT)
    # struct itimerval: two timevals, ITIMER_REAL is 0.
    @setitimer.call(0, [0, microseconds, 0, microseconds].pack("q l x4 q l x4"), nil)
  end

  PTY_MARK = "\0speedrunner: end of load\0".b
  PTY_MARK_WAIT_S = 2.0

  # Nothing left over in a pty stand-in: what a probe wrote and left there, as
  # it stopped at a refused file or was killed, is not the next file's.
  def self.settle_pty(master, slave)
    begin
      loop do
        chunk = master.read_nonblock(65_536, exception: false)
        break if chunk.nil? || chunk == :wait_readable
      end
    rescue Errno::EIO
      nil
    end
    slave.syswrite(PTY_MARK)
    read_to_mark(master, "".b)
  end

  # All that went through a pty stand-in. Its master gets what was written a
  # little later (the kernel's flip buffer), later still on a busy machine:
  # what comes before our own mark, written last, is all of it. The rest would
  # be missing from what each run prints, and blamed on the next file loaded.
  def self.read_to_mark(master, data)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + PTY_MARK_WAIT_S
    until data.include?(PTY_MARK)
      left = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
      break if left <= 0 || !IO.select([master], nil, nil, left)
      chunk = master.read_nonblock(65_536, exception: false)
      break if chunk.nil? || chunk == :wait_readable
      data << chunk
    end
    data.gsub(PTY_MARK, "")
  rescue Errno::EIO
    data.gsub(PTY_MARK, "")
  end

  # One pty per fd of the mask, raw (output bytes unchanged), with the
  # client's terminal size.
  class TtyStandIn
    attr_reader :master, :slave

    def initialize(mask, rows, cols)
      @master, @slave = {}, {}
      [0, 1, 2].each do |fd|
        next if (mask & (1 << fd)).zero?
        master, slave = PTY.open
        slave.raw!
        slave.winsize = [rows, cols] if rows.positive? && cols.positive?
        @master[fd], @slave[fd] = master, slave
      end
    end

    def covers?(fd) = @slave.key?(fd)

    def close
      (@master.values + @slave.values).each(&:close)
      @master, @slave = {}, {}
    end
  end

  # Whether fds 0-2 were found to be terminals while active, e.g. by
  # load-time code (rainbow, for rubocop), which keeps the answer for every
  # later run. Prepended on IO for good, not on the standard IOs: reopen,
  # which we use on them, may change their class. Records only while on.
  module TtyReads
    ON = [false]
    SEEN = {}

    %i[tty? isatty].each do |name|
      define_method(name) do
        result = super()
        if ON[0]
          fd = fileno
          SEEN[fd] = result if fd <= 2 && !SEEN.key?(fd)
        end
        result
      end
    end
  end
  IO.prepend(TtyReads)

  def self.tty_stand_in(spec)
    mask, size = spec.to_s.split(":", 2)
    rows, cols = size.to_s.split("x", 2).map(&:to_i)
    mask = mask.to_i & 7
    mask.zero? ? nil : TtyStandIn.new(mask, rows.to_i, cols.to_i)
  rescue SystemCallError, ArgumentError => e
    log("no terminal stand-in (#{e.inspect}): loads see none")
    nil
  end

  # Ruby 3.3 starts with a timer thread of its own, which fork handles:
  # only threads beyond those we started with count.
  BASE_THREADS = native_thread_count
  def self.threads_running?
    return true if Thread.list.size != 1
    # Each fork replaces that timer thread in the parent too; on a busy
    # machine the old one may still be there for a couple of milliseconds.
    deadline = now + 0.1
    sleep(0.001) while native_thread_count > BASE_THREADS && now < deadline
    native_thread_count > BASE_THREADS
  end

  def self.stat_key(path)
    st = File.stat(path)
    [st.mtime.tv_sec, st.mtime.tv_nsec, st.size, st.ino, st.dev]
  rescue SystemCallError
    nil
  end

  # inotify on the files the preloaded state was built from: see FileWatch
  # in speedrunner_daemon.py. The queue is never read; a pending event means
  # stale for every child and for the parent.
  class FileWatch
    MASK = 0x2 | 0x4 | 0x400 | 0x800 # MODIFY, ATTRIB, DELETE_SELF, MOVE_SELF
    attr_reader :io

    def initialize
      libc = Fiddle.dlopen(nil)
      init = Fiddle::Function.new(libc["inotify_init1"], [Fiddle::TYPE_INT], Fiddle::TYPE_INT)
      @add = Fiddle::Function.new(libc["inotify_add_watch"],
                                  [Fiddle::TYPE_INT, Fiddle::TYPE_VOIDP, Fiddle::TYPE_INT],
                                  Fiddle::TYPE_INT)
      fd = init.call(File::NONBLOCK | IN_CLOEXEC)
      @io = fd >= 0 ? IO.for_fd(fd, autoclose: false) : nil
    end

    def add(path) = !@io.nil? && @add.call(@io.fileno, path, MASK) >= 0
    def fired? = !@io.nil? && !IO.select([@io], nil, nil, 0).nil?
  end

  def self.fingerprint_paths(state)
    gem_dirs = Gem.path.flat_map { |d| [d, File.join(d, "specifications")] }
    ([state.script, RbConfig.ruby] + gem_dirs + $LOAD_PATH.map(&:to_s)).select { |p| File.exist?(p) }
  end

  def self.extend_fingerprint(state, paths, watch: false)
    fresh = paths.uniq.reject { |p| state.fingerprint_set.key?(p) }
    fresh.each { |p| state.fingerprint_set[p] = true }
    if watch && !MACOS # no inotify: stat
      state.watch ||= FileWatch.new
      fresh = fresh.reject { |p| state.watch.add(p) }
    end
    state.fingerprint_paths += fresh
    state.fingerprint += fresh.map { |p| stat_key(p) }
  end

  def self.stale?(state)
    (state.watch&.fired? || false) || state.fingerprint_paths.map { |p| stat_key(p) } != state.fingerprint ||
      !state.import_dirs.all? { |p| File.directory?(p) }
  end

  def self.preload(state)
    names = []
    if state.levels.include?("1")
      names = top_level_requires(state.script)
      # A RubyGems binstub: activate the gem as the binstub would, then
      # preload what the gem's own executable requires at top level.
      binstub = File.read(state.script).match(/Gem\.activate_bin_path\(['"]([^'"]+)['"], ['"]([^'"]+)['"]/)
      state.gem_binstub = !binstub.nil?
      if binstub
        gem_name, exe = binstub[1], binstub[2]
        names << -> { top_level_requires(Gem.activate_bin_path(gem_name, exe, ">= 0.a")) }
      end
    end
    state.pre_import_env = ENV.to_h
    t0 = now
    begin
      EnvReads::ON[0] = true
      skip = state.skip = load_skip(state)
      TtyReads::SEEN.clear
      TtyReads::ON[0] = true
      state.import_output = reading_files(state) { capture_fds(state.tty) do
        catch(:restart) do
          names.each do |name|
            (name.is_a?(Proc) ? name.call : [name]).each do |req|
              next if skip.key?(req)

              why = if begin
                         reads_argv { require req }[1]
                       rescue Refused => e
                         LOCK_SEEN[0] = true
                         log("#{req}: #{e.message}")
                         false
                       end then "reads the command line at load"
                    elsif LOCK_SEEN[0] then LOCK_AT_LOAD
                    elsif threads_running? then "starts a thread at load"
                    elsif cf_unsafe? then "uses CoreFoundation at load"
                    end
              next unless why

              # Already run with the daemon's command line, or a thread or
              # CoreFoundation that forbid forking, which cannot be undone: skip it from now on,
              # in a fresh daemon. Level 2 still learns what it loads, short
              # of what starts the thread.
              skip[req] = why
              save_skip(state, skip)
              state.restart = "#{req} #{why}"
              throw :restart
            end
          end
        end
      end }
    rescue Exception => e # rubocop:disable Lint/RescueException
      state.disabled = "preload failed: #{e.inspect}"
      log(state.disabled)
    ensure
      EnvReads::ON[0] = false
      TtyReads::ON[0] = false
    end
    return if state.restart

    state.import_env_read = EnvReads::KEYS.uniq
    state.import_tty_read = TtyReads::SEEN.dup
    after = ENV.to_h
    state.import_env_changed = after.reject { |k, v| state.pre_import_env[k] == v }
    state.import_env_deleted = state.pre_import_env.keys - after.keys
    state.disabled ||= "threads running after preload" if threads_running?
    extend_fingerprint(state, fingerprint_paths(state))
    extend_fingerprint(state, $LOADED_FEATURES.select { |f| f.start_with?("/") }, watch: true)
    state.cache_path = cache_file(state)
    keep_file_reads(state)
    if state.levels.include?("2") && !state.disabled
      cached = load_cache(state)
      state.blacklist = (cached["blacklist"] || {}).select { |k, v| k.is_a?(String) && v.is_a?(String) }
      state.learned_gems = Array(cached["gems"]).select { |g| g.is_a?(Array) && g.size == 2 }
      activate_gems(state.learned_gems)
      files = Array(cached["modules"]).grep(String).reject { |f| state.blacklist.key?(f) }
      # Vetted when they were learned: required directly, but checked again.
      why = import_in_daemon(state, files)
      save_cache(state) if why || state.learned.size != files.size
      state.restart = why
      extend_fingerprint(state, $LOADED_FEATURES.select { |f| f.start_with?("/") }, watch: true)
    end
    state.preload_s = now - t0
  end

  # --- level 2: learned gems and files -------------------------------------

  def self.activate_gems(gems)
    gems.each do |name, version|
      next if Gem.loaded_specs[name]&.version&.to_s == version
      Gem::Specification.find_by_name(name, "= #{version}").activate
    rescue Gem::LoadError, Gem::MissingSpecError
      nil # conflicts with what is active: the child activates as natively
    end
  end

  # Require vetted files in the daemon, in order. A file that fails is
  # blacklisted. Returns why the daemon must restart, or nil.
  def self.import_in_daemon(state, files)
    loaded = []
    files.each do |file|
      next if state.blacklist.key?(file)
      unless $LOADED_FEATURES.include?(file)
        loaded << file
        env_before = ENV.to_h
        begin
          EnvReads::ON[0] = true
          TtyReads::SEEN.clear
          TtyReads::ON[0] = true
          output, argv_read = reads_argv { reading_files(state) { capture_fds(state.tty) { require file } } }
          keep_tool_traps(state)
        rescue Refused => e
          state.blacklist[file] = e.message
          return "#{file} #{e.message}"
        rescue Exception => e # rubocop:disable Lint/RescueException
          state.blacklist[file] = "load failed in the daemon: #{e.class}"
          next
        ensure
          EnvReads::ON[0] = false
          TtyReads::ON[0] = false
          TtyReads::SEEN.each { |fd, was| state.import_tty_read[fd] = was unless state.import_tty_read.key?(fd) }
        end
        keep_file_reads(state)
        why = if LOCK_SEEN[0] then LOCK_AT_LOAD
              elsif output != ["".b, "".b] then "printed in the daemon"
              elsif argv_read then "read the command line in the daemon"
              elsif ENV.to_h != env_before then "changed the env in the daemon"
              elsif threads_running? then "started a thread in the daemon"
              end
        if why # cannot be undone
          state.blacklist[file] = why
          return "#{file} #{why}"
        end
      end
      state.learned << file unless state.learned.include?(file)
    end
    state.import_env_read |= EnvReads::KEYS.uniq
    if loaded.any? && cf_unsafe?
      # Vetted one by one, yet together they did: none of them again.
      loaded.each { |file| state.blacklist[file] = "used CoreFoundation in the daemon, with others" }
      return "#{loaded.size} files used CoreFoundation in the daemon"
    end
    nil
  end

  # Raised in a probe when a file already refused starts loading: the file
  # that requires it is refused too, without running the refused one again,
  # which would cost as much each time. Not a StandardError, so that an
  # optional require (`rescue LoadError`) cannot hide it.
  class Refused < Exception; end # rubocop:disable Lint/InheritException

  # Where a file belongs: its gem's directory, else its own.
  def self.load_root(path)
    path[%r{\A.*/gems/[^/]+/}] || "#{File.dirname(path)}/"
  end

  # The namespaces each file opens (`module`/`class`) while it loads, by
  # name: path -> names, filled while the returned TracePoint is enabled.
  # Only statements of the file's own body: one in a method runs whenever
  # the method is called (metaid.rb's `class << self`), from other files.
  # Addresses, in the names of anonymous classes, differ in every process.
  # Module's own methods: a class may redefine name (puppet's features).
  # Never raises: that would fail the load it watches.
  MODULE_NAME = Module.instance_method(:name)
  MODULE_INSPECT = Module.instance_method(:inspect)

  def self.namespace_tracer(traces)
    TracePoint.new(:class) do |t|
      scope = caller_locations(2, 1)&.first&.label.to_s # where the statement runs
      next unless scope.start_with?("<", "block in <")

      mod = t.self
      name = MODULE_NAME.bind_call(mod) || MODULE_INSPECT.bind_call(mod)
      traces[t.path] << name.to_s.gsub(/0x\h+/, "0x")
    rescue Exception # rubocop:disable Lint/RescueException
      traces[t.path] << "?"
    end
  end

  # Runs the block in a throwaway child; returns what it returned, through
  # JSON, or fallback if the child crashed or took longer than the probe
  # timeout.
  def self.in_child(fallback)
    r, w = IO.pipe
    pid = Process.fork
    if pid.nil?
      r.close
      reads = FileReads.new(nil, nil)
      reads.note_mkdirs
      w.write(JSON.generate(yield))
      w.close
      reads.remove_made
      exit!(0)
    end
    w.close
    data = "".b
    deadline = now + PROBE_TIMEOUT_S
    loop do
      unless IO.select([r], nil, nil, [deadline - now, 0].max)
        Process.kill(:KILL, pid)
        data = ""
        break
      end
      chunk = r.read_nonblock(65_536, exception: false)
      break if chunk.nil?
      data << chunk unless chunk == :wait_readable
    end
    r.close
    Process.wait(pid)
    JSON.parse(data)
  rescue JSON::ParserError
    fallback
  end

  # Require files one by one in a throwaway child; sort them into ok, bad
  # (with the reason) and retry: after a bad one, a fresh probe judges the
  # rest, since whatever it left behind would taint later verdicts. A file
  # that loads one of refused is bad. So is one that opens other namespaces
  # than expect has for it, as natively (see native_namespaces).
  #
  # On macOS, once they are loaded, whether CoreFoundation was used (see
  # probe_imports in speedrunner_daemon.py): if so, the verdict is cf, all
  # to retry with each, which checks after each file.
  def self.probe(gems, files, tty = nil, refused = [], expect = {}, each: false)
    in_child({ "ok" => [], "bad" => files.to_h { |f| [f, "probe crashed or hung"] }, "retry" => [] }) do
      verdict = { "ok" => [], "bad" => {}, "retry" => [] }
      activate_gems(gems)
      refused = refused.to_h { |f| [f, true] }
      FileReads.new(nil, nil).refuse_locks
      compiled = {}
      TracePoint.new(:script_compiled) do |t|
        next unless t.eval_script.nil?

        path = t.instruction_sequence.path
        raise Refused, "loads #{path}, refused" if refused.key?(path)

        compiled[path] = true
      end.enable
      traces = Hash.new { |h, k| h[k] = [] }
      namespace_tracer(traces).enable
      unchecked = expect.keys
      # The first file loaded since the last call whose namespaces differ
      # from those expected, with why: file is the one being required.
      mismatch = lambda do |file|
        done, unchecked = unchecked.partition { |f| compiled.key?(f) }
        f = done.find { |g| expect[g] != traces[g] }
        "#{f == file ? "opens" : "loads #{f}, which opens"} other namespaces than natively" if f
      end
      step_end = now + PROBE_STEP_S
      files.each_with_index do |file, i|
        if verdict["ok"].any? && now > step_end
          verdict["retry"] = files[i..] # the parent replaces the spare in between
          break
        end
        if $LOADED_FEATURES.include?(file)
          verdict["ok"] << file # came with an earlier, clean require
          next
        end
        env_before = ENV.to_h
        why = begin
          output, argv_read = reads_argv { capture_fds(tty) { require file } }
          if LOCK_SEEN[0] then LOCK_AT_LOAD
          elsif output != ["".b, "".b] then "prints at load" # natively mid-run
          elsif argv_read then "reads the command line at load"
          elsif ENV.to_h != env_before then "changes the env at load"
          elsif threads_running? then "starts a thread at load"
          elsif (m = mismatch.call(file)) then m
          elsif each && cf_unsafe? then "uses CoreFoundation at load"
          end
        rescue Refused => e
          e.message
        rescue Exception => e # rubocop:disable Lint/RescueException
          "load failed: #{e.class}: #{e.message[0, 200]}"
        end
        if why
          verdict["bad"][file] = why
          verdict["retry"] = files[(i + 1)..]
          break
        end
        verdict["ok"] << file
      end
      # With threads (a bad one's), the canary itself is unreliable.
      if MACOS && !each && verdict["ok"].any? && (threads_running? || cf_unsafe?)
        verdict = { "ok" => [], "bad" => {}, "retry" => files, "cf" => true }
      end
      verdict
    end
  end

  # Natively, every file runs, refused ones included, and a later file may
  # need what an earlier one defined: fastlane.rb sets Fastlane::Helper to
  # FastlaneCore::Helper, which dotenv_helper.rb, loaded later, reopens;
  # preloaded without fastlane.rb, it makes a Fastlane::Helper of its own.
  # In a throwaway child, load files in their native order whatever they do:
  # path -> the namespaces it opened, to compare in the probe.
  def self.native_namespaces(state, files)
    in_child({}) do
      traces = Hash.new { |h, k| h[k] = [] }
      failed = {}
      capture_fds(state.tty) do
        namespace_tracer(traces).enable
        files.each do |f|
          require f
        rescue Exception # rubocop:disable Lint/RescueException
          failed[f] = true
        end
      end
      files.to_h { |f| [f, failed[f] ? ["(fails)"] : traces[f]] }
    end
  end

  def self.cache_file(state)
    base = ENV["XDG_CACHE_HOME"].to_s.empty? ? File.join(Dir.home, ".cache") : ENV["XDG_CACHE_HOME"]
    # 2: from 0.4.3, nothing of a project; an earlier cache may hold some.
    key = Digest::SHA256.hexdigest([2, state.script, RbConfig.ruby, RUBY_VERSION, state.fingerprint].inspect)
    File.join(base, "speedrunner", "#{state.tool}-#{key[0, 16]}.json")
  end

  def self.load_cache(state)
    data = JSON.parse(File.read(state.cache_path))
    data.is_a?(Hash) ? data : {}
  rescue SystemCallError, JSON::ParserError
    {}
  end

  def self.save_cache(state)
    tmp = "#{state.cache_path}.#{Process.pid}.tmp"
    FileUtils_mkdir_p(File.dirname(state.cache_path))
    File.write(tmp, JSON.pretty_generate("modules" => state.learned, "gems" => state.learned_gems,
                                         "blacklist" => state.blacklist))
    File.rename(tmp, state.cache_path)
  rescue SystemCallError => e
    log("cannot write #{state.cache_path}: #{e.inspect}")
  end

  # Level-1 requires never to preload. Keyed by the script alone: they are
  # known before the requires that compute the fingerprint.
  def self.skip_file(state)
    base = ENV["XDG_CACHE_HOME"].to_s.empty? ? File.join(Dir.home, ".cache") : ENV["XDG_CACHE_HOME"]
    key = Digest::SHA256.hexdigest([state.script, stat_key(state.script), RbConfig.ruby, RUBY_VERSION].inspect)
    File.join(base, "speedrunner", "#{state.tool}.skip-#{key[0, 16]}.json")
  end

  def self.load_skip(state)
    data = JSON.parse(File.read(skip_file(state)))
    data.is_a?(Hash) ? data.select { |k, _| k.is_a?(String) } : {}
  rescue SystemCallError, JSON::ParserError
    {}
  end

  def self.save_skip(state, skip)
    path = skip_file(state)
    tmp = "#{path}.#{Process.pid}.tmp"
    FileUtils_mkdir_p(File.dirname(path))
    File.write(tmp, JSON.pretty_generate(skip))
    File.rename(tmp, path)
  rescue SystemCallError => e
    log("cannot write #{path}: #{e.inspect}")
  end

  def self.FileUtils_mkdir_p(dir) # rubocop:disable Naming/MethodName
    return if File.directory?(dir)
    FileUtils_mkdir_p(File.dirname(dir))
    Dir.mkdir(dir, 0o700)
  rescue Errno::EEXIST
    nil
  end

  # The files level 1 skipped (it skips requires by name).
  def self.skipped_paths(state)
    state.skip.keys.filter_map do |req|
      kind, path = $LOAD_PATH.resolve_feature_path(req)
      path if kind == :rb
    rescue LoadError
      nil
    end
  end

  # Vet and load what children learned, then let the caller replace the
  # spare. One probe per call: between calls the main loop keeps a spare
  # ready. Returns [changed, reason to restart or nil].
  def self.apply_learned(state)
    job = state.learning
    unless job
      gems = state.pending_gems.keys - state.learned_gems
      native = state.pending.keys.reject { |f| $LOADED_FEATURES.include?(f) }
      files = native.reject { |f| state.blacklist.key?(f) }
      state.pending.clear
      state.pending_gems.clear
      before = [state.learned.size, state.blacklist.size, state.learned_gems.size]
      activate_gems(gems)
      state.learned_gems |= gems
      # What level 1 skipped loaded before all of these, natively.
      expect = files.empty? ? {} : native_namespaces(state, skipped_paths(state) + native)
      job = state.learning = { files: files, before: before, t0: now, expect: expect, vets: files.any? }
      # Replace the spare: one forked from now on says so in its status.
      return [true, nil] if files.any?
    end
    if job[:files].any?
      verdict = probe(state.learned_gems, job[:files], state.tty, state.blacklist.keys + skipped_paths(state),
                      job[:expect], each: job[:each])
      job[:each] ||= verdict["cf"] # from then on, for this job
      state.blacklist.update(verdict["bad"])
      why = import_in_daemon(state, verdict["ok"])
      if why
        state.learning = nil
        save_cache(state)
        return [true, why]
      end
      extend_fingerprint(state, $LOADED_FEATURES.select { |f| f.start_with?("/") }, watch: true)
      job[:files] = verdict["retry"].reject { |f| state.blacklist.key?(f) || $LOADED_FEATURES.include?(f) }
      return [false, nil] if job[:files].any?
    end
    state.learning = nil
    # Nothing new: a spare that said it was learning is replaced all the same.
    return [job[:vets], nil] if [state.learned.size, state.blacklist.size, state.learned_gems.size] == job[:before]
    state.gen += 1
    save_cache(state)
    log("learned in #{((now - job[:t0]) * 1000).round} ms; #{status_text(state)}")
    [true, nil]
  end

  # In a child: files in the order their loading started (as required:
  # $LOADED_FEATURES has them in the order they finished, and replaying
  # that breaks circular requires), and the gems this run activated.
  def self.start_learning
    roots = learn_roots
    base_features = $LOADED_FEATURES.to_h { |f| [f, true] }
    base_gems = Gem.loaded_specs.keys
    order = []
    tp = TracePoint.new(:script_compiled) do |t|
      order << t.instruction_sequence.path if t.eval_script.nil?
    end
    tp.enable
    CHILD[:learn] = lambda do
      tp.disable
      loaded = $LOADED_FEATURES.reject { |f| base_features.key?(f) }
      ordered = order.select { |f| loaded.include?(f) }.uniq
      files = ordered + (loaded - ordered) # native extensions compile nothing
      files = files.select { |f| in_roots?(f, roots) }
      gems = (Gem.loaded_specs.keys - base_gems).map { |n| [n, Gem.loaded_specs[n].version.to_s] }
      { "modules" => files, "gems" => gems }
    end
  end

  # Where the tool's own files live: the load path it was preloaded with and
  # the gem directories. Never / or the home directory, which would take in
  # every project: rspec puts a project's directories on the load path and
  # requires its spec_helper, which must not configure every later run.
  def self.learn_roots
    skip = ["/", (File.realpath(Dir.home) rescue Dir.home)]
    dirs = $LOAD_PATH.map(&:to_s) + Gem.path
    dirs.filter_map do |d|
      next unless d.start_with?("/") && File.directory?(d)
      root = File.realpath(d)
      root unless skip.include?(root)
    rescue SystemCallError
      nil
    end.uniq
  end

  def self.in_roots?(path, roots)
    real = File.realpath(path)
    roots.any? { |r| real == r || real.start_with?(r.end_with?("/") ? r : "#{r}/") }
  rescue SystemCallError
    false
  end

  def self.send_learned
    learn = CHILD[:learn] or return
    data = learn.call
    return if data["modules"].empty? && data["gems"].empty?
    send_msg(CHILD[:ctl], "L", JSON.generate(data))
  rescue IOError, SystemCallError
    nil
  end

  # --- child: one invocation -----------------------------------------------

  def self.why_not_fast(state, req, peer_gid, fds, peer_groups = nil)
    return state.disabled if state.disabled
    return "script path mismatch" unless req.argv[0] == b(state.script)
    if state.gem_binstub && req.argv[1].to_s =~ /\A_(.*)_\z/n && Gem::Version.correct?($1)
      return "gem version chosen on the command line"
    end
    return "stale: installed files changed" if stale?(state)
    why = why_cwd_differs(state, fds[3])
    return why if why

    groups = peer_groups || req.groups
    if peer_gid != Process.gid || groups.uniq.sort != own_groups.uniq.sort
      return "credentials differ from the daemon's"
    end
    client = startup_env(req.env)
    daemon = startup_env(state.start_env)
    (client.keys | daemon.keys).sort.each do |key|
      return "startup env differs: #{key}" if client[key] != daemon[key]
    end
    frozen = state.import_env_changed.keys | state.import_env_deleted | state.import_env_read
    frozen.sort.each do |key|
      pre = state.pre_import_env[key]
      theirs = without_links(state, key, req.env[b(key)])
      return "import-time env differs: #{key}" if theirs != without_links(state, key, pre && b(pre))
    end
    state.import_tty_read.sort.each do |fd, was|
      return "import-time terminal state differs: fd #{fd}" if fds[fd].tty? != was
    end
    nil
  end

  # Opens of /dev/tty, and IO.console, reach the client's terminal: the
  # child has no controlling terminal of its own, and io/console's getpass
  # would otherwise fail or read a pipe.
  def self.bridge_tty(tty)
    dup_fd = lambda do
      io = tty.dup
      io.autoclose = false
      io.fileno
    end
    console = [nil]
    IO.singleton_class.prepend(Module.new do
      define_method(:console) do |*args, **kw|
        return super(*args, **kw) unless args.empty? || args == [:close]
        if args == [:close]
          console[0]&.close
          console[0] = nil
        else
          console[0] ||= File.new(dup_fd.call, "r+")
        end
      end
    end)
    File.singleton_class.prepend(Module.new do
      %i[open new].each do |name|
        define_method(name) do |path, *rest, **kw, &blk|
          path = dup_fd.call if path.is_a?(String) && path == "/dev/tty"
          super(path, *rest, **kw, &blk)
        end
      end
    end)
  end

  # Files the run opens: teardown would flush them; we must, before X.
  def self.track_files
    files = ObjectSpace::WeakMap.new
    CHILD[:files] = files
    tracker = Module.new do
      define_method(:initialize) do |*args, **kw, &blk|
        super(*args, **kw, &blk)
        files[self] = true
      end
    end
    IO.prepend(tracker)
    File.prepend(tracker)
  end

  def self.apply_request(state, req, fds)
    stdin, stdout, stderr, cwd = fds[0, 4]
    state.tty&.close
    [STDOUT, STDERR].each(&:flush)
    # reopen takes the encodings of the IO it is given: keep the startup ones.
    encodings = [STDIN, STDOUT, STDERR].map { |io| [io.external_encoding, io.internal_encoding] }
    STDIN.reopen(stdin)
    STDOUT.reopen(stdout)
    STDERR.reopen(stderr)
    [STDIN, STDOUT, STDERR].zip(encodings) do |io, (ext, int)|
      io.set_encoding(ext, int) if [io.external_encoding, io.internal_encoding] != [ext, int]
    end
    STDOUT.sync = false # as at startup: STDERR alone is synchronous
    STDERR.sync = true
    chdir_to(cwd)
    fds[0, 4].each(&:close)
    bridge_tty(fds[4]) if fds.size > 4 # kept open, close-on-exec, for the run
    File.umask(req.umask)
    keep_load_dirs(state)
    # Adapter: rspec keeps the cwd it loaded in, to tell the project's files.
    if defined?(RSpec::Core::FilterRules::PROJECT_DIR)
      RSpec::Core::FilterRules.send(:remove_const, :PROJECT_DIR)
      RSpec::Core::FilterRules.const_set(:PROJECT_DIR, File.expand_path("."))
      RSpec.configuration.backtrace_formatter.send(:initialize)
    end
    # Adapter: rspec reports its load time from a clock read when rspec/core
    # loads; for this run, that moment is now.
    $_rspec_core_load_started_at = Time.now if global_variables.include?(:$_rspec_core_load_started_at)

    # In place: ENV.replace calls setenv/unsetenv, for C code and children.
    enc = Encoding.find("locale")
    ENV.replace(req.env.to_h { |k, v| [k.dup.force_encoding(enc), v.dup.force_encoding(enc)] })
    state.import_env_deleted.each { |k| ENV.delete(k) }
    ENV.update(state.import_env_changed)

    # Dispositions a native interpreter would start with: Ruby's own, plus
    # the ignored signals the client inherited.
    %w[CHLD TERM USR1 INT HUP QUIT].each { |sig| trap(sig, "DEFAULT") }
    (1..63).each do |n|
      next if req.ignored_signals[n].zero?
      name = Signal.signame(n) or next
      begin
        trap(name, "IGNORE")
      rescue ArgumentError
        nil # reserved by Ruby
      end
    end
    # What the tool's files set as they loaded, after what it inherits, as natively.
    state.tool_traps.each { |sig, handler| trap(sig, handler) }

    $0 = state.script
    ARGV.replace(req.argv[1..].map { |a| a.dup.force_encoding(enc) })
  end

  # Natively, the backtrace starts in the script, whose frames are <main>:
  # drop ours, and rename the script's frames from <top (required)>.
  # What Ruby's own error printer does to an uncaught exception's message,
  # and full_message does not: backslashes and control characters escaped,
  # tab and newline aside (CocoaPods raises messages with colour codes).
  MESSAGE_ESCAPES = { "\\" => "\\\\", "\a" => "\\a", "\b" => "\\b", "\v" => "\\v", "\f" => "\\f",
                      "\r" => "\\r", "\e" => "\\e", "\x7F" => "\\c?" }.freeze

  # Ruby 3.2 and later print the message as is (through detailed_message).
  ESCAPES_MESSAGE = (RUBY_VERSION.split(".").map(&:to_i) <=> [3, 2]).negative?

  def self.escape(text)
    text.gsub(/[\x00-\x08\x0B-\x1F\x7F\\]/n) { |c| MESSAGE_ESCAPES[c] || format("\\x%02X", c.ord) }
  end

  # full_message, with the message escaped in place, line by line: the
  # message may come from an overridden #message (CocoaPods' CLAide::Help),
  # and on a terminal each of its lines is wrapped in highlighting.
  def self.error_text(err)
    full = err.full_message
    message = err.message
    return full unless ESCAPES_MESSAGE && message.is_a?(String)

    out = full.b
    pos = 0
    message.b.split("\n").each do |line|
      escaped = escape(line)
      next if escaped == line

      i = out.index(line, pos) or next
      out[i, line.bytesize] = escaped
      pos = i + escaped.bytesize
    end
    out.force_encoding(full.encoding)
  end

  def self.clean_backtrace(err, script)
    bt = err.backtrace or return
    err.set_backtrace(bt.reject { |f| f.start_with?("#{DAEMON_FILE}:") }.map do |f|
      # Ruby 3.4 quotes the label '<main>', earlier versions `<main>'.
      f.start_with?("#{script}:") ? f.sub(/in ([`'])<top \(required\)>'/, "in \\1<main>'") : f
    end)
  end

  # at_exit handler registered before anything else, so it runs last.
  def self.report_exit
    conn = CHILD[:conn] or return
    return unless Process.pid == CHILD[:pid] # a process the tool forked, which ends on its own
    err = $!
    clean_backtrace(err, CHILD[:script]) if err
    CHILD[:files]&.each_key do |io|
      io.flush unless io.closed?
    rescue IOError, SystemCallError
      nil # teardown will retry and report it
    end
    if err.is_a?(Errno::EPIPE)
      # `| head`: natively Ruby dies of SIGPIPE, silently, as its STDOUT is marked
      # for it. Reopening it lost that mark.
      trap("PIPE", "SYSTEM_DEFAULT")
      Process.kill("PIPE", Process.pid)
      sleep
    end
    begin
      [$stdout, $stderr, STDOUT, STDERR].uniq.each { |io| io.flush unless io.closed? }
    rescue IOError, SystemCallError
      return # natively the exit reports it: let the wait status tell
    end
    code = case err
           when nil then 0
           when SystemExit then err.status
           when SignalException then return # Ruby prints it and dies by it: W
           else
             # Ruby prints an uncaught exception after the at_exit handlers.
             STDERR.write(error_text(err))
             1
           end
    tell_parent("E") # before the client can leave
    begin
      send_msg(conn, "X", [code].pack("l<"))
    rescue IOError, SystemCallError
      return
    end
    # The client is gone from here on; pipes it shares must see EOF now.
    null = File.open(File::NULL, "r+")
    [STDIN, STDOUT, STDERR].each { |io| io.reopen(null) }
    send_learned
  end

  def self.run_tool(state, conn)
    # Natively printed by the requires on every run.
    STDOUT.syswrite(state.import_output[0]) unless state.import_output[0].empty?
    STDERR.syswrite(state.import_output[1]) unless state.import_output[1].empty?
    CHILD[:conn] = conn
    CHILD[:pid] = Process.pid
    CHILD[:script] = state.script
    track_files
    start_learning if state.levels.include?("2")
    at_exit { CHILD[:exiting] = true } if CHILD[:load_dirs] # see keep_load_dirs
    load state.script # exceptions and exit go up to Ruby, as natively
  end

  # R: the tool runs, its client may only leave by dying. E: it may leave.
  GC_ROOM_FRACTION = 16

  # A child that finds this heap nearly full collects it itself, at each call
  # until the parent does: 3 to 8 ms for a small tool. Just after a fork: the
  # spare is out, so no client waits for this, not even the first.
  #
  # By what this process allocated since its last collection, against what that
  # one freed: the free slots of the smallest objects, which fill first, are
  # not published (GC.stat adds up all sizes).
  def self.collect_for_next_fork(state)
    allocated = GC.stat(:total_allocated_objects)
    return if allocated < state.gc_next

    GC.start
    state.gc_next = allocated + GC.stat(:heap_free_slots) / GC_ROOM_FRACTION
  end

  def self.tell_parent(kind)
    send_msg(CHILD[:ctl], kind, "")
  rescue IOError, SystemCallError
    nil # the daemon is gone: the child goes on alone
  end

  def self.serve_child(state, conn)
    deadline = now + HANDSHAKE_TIMEOUT_S
    begin
      uid, gid, groups = peer_creds(conn)
      exit!(0) unless uid == Process.uid
      begin
        send_msg(conn, "H") # now that someone is here to read: see the client
        kind, body, fds = recv_request(conn, deadline)
      rescue EOFError, Errno::EPIPE
        exit!(0) # a client that only checked we are up, or gave up on us
      end
      if kind == "P"
        if stale?(state)
          mark_exiting(state)
          unlink_socket(state)
          Process.kill(:USR1, PARENT_PID)
          send_msg(conn, "P", "stale: installed files changed")
        else
          send_msg(conn, "P", status_text(state))
        end
        exit!(0)
      end
      if kind == "S"
        send_msg(conn, "P", "stopping")
        Process.kill(:TERM, PARENT_PID)
        exit!(0)
      end
      req = parse_request(body)
      unless fds.size.between?(4, 5)
        send_msg(conn, "N", "missing file descriptors")
        exit!(0)
      end
      reason = why_not_fast(state, req, gid, fds, groups)
      if reason
        if reason.start_with?("stale")
          mark_exiting(state)
          unlink_socket(state)
          Process.kill(:USR1, PARENT_PID)
        end
        send_msg(conn, "N", reason)
        exit!(0)
      end
      send_msg(conn, "R", [Process.pid].pack("l<"))
      raise "no go from the client" unless read_exact(conn, 1, deadline) == "G"
      tell_parent("R")
      apply_request(state, req, fds)
    rescue SignalException => e
      # Relayed by a client that has our pid: natively, the tool would die of it.
      trap(e.signo, "SYSTEM_DEFAULT")
      Process.kill(e.signo, Process.pid)
      sleep
    rescue Exception => e # rubocop:disable Lint/RescueException
      log("child setup failed: #{e.inspect}")
      # Nothing has run. Our exit would not do: past G, the client takes our
      # wait status for the tool's, and a run that never started for a success.
      begin
        send_msg(conn, "N", "child setup failed: #{e.class}")
      rescue IOError, SystemCallError
        nil # the client is gone
      end
      exit!(0)
    end
    LOG.close
    run_tool(state, conn)
  end

  def self.status_text(state)
    return "disabled: #{state.disabled}" if state.disabled
    "ready pid=#{PARENT_PID} preload_ms=#{(state.preload_s * 1000).round} " \
      "modules=#{$LOADED_FEATURES.size} levels=#{state.levels.join(',').then { |l| l.empty? ? 'none' : l }} " \
      "learned=#{state.learned.size} blacklisted=#{state.blacklist.size} memo=0 gen=#{state.gen} " \
      "tty_read=#{state.import_tty_read.keys.sort.join(',').then { |t| t.empty? ? '-' : t }}" \
      "#{" learning=#{state.learning[:files].size}" if state.learning}"
  end

  # --- parent: accept loop -------------------------------------------------

  def self.open_runtime_dir(path)
    # Ruby has no File::DIRECTORY, and O_DIRECTORY's value depends on the
    # architecture (0o200000 on x86, O_DIRECT on arm64): check the type on
    # the open fd instead. NONBLOCK: a FIFO put there must not block us.
    # Ruby opens close-on-exec by itself.
    dir = File.open(path, File::RDONLY | File::NOFOLLOW | File::NONBLOCK)
    st = dir.stat
    unless st.directory? && st.uid == Process.uid && (st.mode & 0o7777) == 0o700
      dir.close
      raise "#{path} must be a directory owned by uid #{Process.uid} with mode 0700"
    end
    # Ruby 3.1 has no fchdir: chdir by path, then check it is still that one.
    Dir.chdir(path)
    here = File.stat(".")
    raise "#{path} changed under us" unless here.ino == st.ino && here.dev == st.dev
    dir
  end

  def self.acquire_lock(state)
    if state.lock_fd
      lock = File.for_fd(state.lock_fd, autoclose: true)
      lock.close_on_exec = true
      lock.flock(File::LOCK_EX | File::LOCK_NB) # ours already: a no-op
      return lock
    end
    lock = File.open("#{state.sock_path}.lock", File::RDWR | File::CREAT, 0o600)
    deadline = now + LOCK_WAIT_S
    until lock.flock(File::LOCK_EX | File::LOCK_NB)
      if now > deadline
        lock.close
        return nil
      end
      sleep 0.05
    end
    lock
  end

  # See without_links in speedrunner_daemon.py.
  def self.without_links(state, key, value)
    return value if key != "PATH" || value.nil?

    links = b(File.dirname(state.sock_path))
    value.split(":", -1).reject do |d|
      d = d.chomp("/")
      File.dirname(d) == links && File.basename(d).start_with?("bin-")
    end.join(":")
  end

  # See mark_exiting in speedrunner_daemon.py.
  def self.mark_exiting(state)
    File.open("#{File.basename(state.sock_path)}.lock", File::WRONLY) do |f|
      f.truncate(0)
      f.syswrite("exiting #{PARENT_PID}\n")
    end
  rescue SystemCallError
    nil
  end

  def self.unlink_socket(state)
    name = File.basename(state.sock_path)
    File.unlink(name) if File.stat(name).ino == state.bound_ino
  rescue SystemCallError
    nil
  end

  OWN_TRAPS = %w[CHLD TERM USR1 INT HUP QUIT].freeze

  # What files trapped as they loaded, for the children to have, and the
  # daemon's own back in place.
  def self.keep_tool_traps(state)
    OWN_TRAPS.each do |sig|
      ours = state.daemon_traps[sig]
      previous = trap(sig, ours || "DEFAULT")
      next if previous.equal?(ours)
      state.tool_traps[sig] = previous if previous.respond_to?(:call) || %w[IGNORE SIG_IGN EXIT].include?(previous)
    end
  end

  def self.wait_status_payload(status)
    status.signaled? ? [1, status.termsig].pack("Cl<") : [0, status.exitstatus].pack("Cl<")
  end

  def self.handle_inline(state, conn)
    send_msg(conn, "H")
    kind, _body, fds = recv_request(conn, now + 0.5)
    fds.each(&:close)
    case kind
    when "P" then send_msg(conn, "P", status_text(state))
    when "S"
      send_msg(conn, "P", "stopping")
      return false
    else send_msg(conn, "N", state.disabled)
    end
    true
  rescue IOError, EOFError, SystemCallError, ArgumentError
    true
  ensure
    conn.close
  end

  Child = Struct.new(:pid, :ctl, :conn, :retired, :running) do
    def close
      [ctl, conn].each { |io| io&.close unless io&.closed? }
      self.ctl = self.conn = nil
    end
  end

  # One message from a child's control socket: [kind, payload, ios], kind
  # nil at end of file.
  def self.read_ctl(sock)
    data, _addr, _flags, *controls = sock.recvmsg(5, 0, nil, scm_rights: true)
    ios = controls.flat_map { |c| c.unix_rights || [] }
    return [nil, "", ios] if data.nil? || data.empty?
    data = data.b + read_exact(sock, 5 - data.bytesize)
    [data.byteslice(0, 1), read_exact(sock, data.byteslice(1, 4).unpack1("V")), ios]
  end

  # Runs in a forked spare: wait for one client, then serve it.
  def self.run_spare(state, server, ctl, inherited)
    %w[CHLD TERM USR1].each { |sig| trap(sig, "DEFAULT") }
    inherited.each { |io| io.close unless io.closed? }
    Process.setpgid(0, 0)
    conn = nil
    loop do
      readable, = IO.select([server, ctl])
      exit!(0) if readable.include?(ctl) # retired, or the daemon is gone
      conn = server.accept_nonblock(exception: false)
      break unless conn == :wait_readable # else another spare took it
    end
    server.close
    # The parent keeps a copy, to send the wait status and replace us.
    ctl.sendmsg("A" + [0].pack("V"), 0, nil, Socket::AncillaryData.unix_rights(conn))
    CHILD[:ctl] = ctl
    serve_child(state, conn)
  end

  def self.serve(state)
    begin
      state.start_cwd = Dir.pwd
      st = File.stat(state.start_cwd)
      state.start_cwd_id = [st.dev, st.ino]
    rescue SystemCallError # no longer there: loads stay in the runtime directory
      state.start_cwd = nil
    end
    dir = state.runtime_dir = open_runtime_dir(File.dirname(state.sock_path))
    lock = acquire_lock(state)
    unless lock
      log("another daemon holds the lock; exiting")
      return
    end
    name = File.basename(state.sock_path)
    File.unlink(name) if File.exist?(name)
    # Bound before preloading, as in speedrunner_daemon.py.
    umask = File.umask(0o077)
    server = UNIXServer.new(name)
    File.chmod(0o600, name)
    File.umask(umask)
    state.bound_ino = File.stat(name).ino

    preload(state)
    if state.restart
      log("exiting: #{state.restart}; the next client starts a fresh daemon")
      mark_exiting(state)
      unlink_socket(state)
      return
    end
    log("preloaded #{state.tool} in #{(state.preload_s * 1000).round} ms, started by " \
        "#{state.origin}; #{status_text(state)}")

    wake_r, wake_w = IO.pipe
    stop = [nil]
    wake = -> { wake_w.write_nonblock(".", exception: false) }
    keep_tool_traps(state)
    state.daemon_traps = { "CHLD" => proc { wake.call },
                           "TERM" => proc { stop[0] ||= "SIGTERM"; wake.call },
                           "USR1" => proc { stop[0] ||= "stale fingerprint"; wake.call } }
    state.daemon_traps.each { |sig, handler| trap(sig, handler) }
    parent_ios = [wake_r, wake_w, lock, dir]
    children = {}
    spare = nil
    spare_failures = 0
    last_activity = now

    exiting = false
    begin
      # while, not loop: the children run the tool from inside this loop, and
      # since Ruby 3.3 Kernel#loop is a frame of its own in their backtraces.
      while true
        # Stopping: out of the way first, so that new clients start a fresh
        # daemon; then the runs in progress, some of which report their end
        # only through our wait status (killed by a signal).
        if stop[0] && !exiting
          exiting = true
          log("exiting: #{stop[0]}")
          mark_exiting(state)
          unlink_socket(state)
          # The lock only says who serves the socket: a new daemon may start
          # now, however long the runs we wait for last.
          lock.close
          state.disabled ||= "exiting"
        end
        break if exiting && children.empty?
        if spare.nil? && !state.disabled
          if threads_running?
            state.disabled = "threads appeared in the daemon"
          else
            [STDOUT, STDERR].each(&:flush)
            ctl, ctl_child = UNIXSocket.pair
            pid = Process.fork
            if pid.nil?
              ctl.close
              inherited = parent_ios + children.values.flat_map { |c| [c.ctl, c.conn].compact }
              run_spare(state, server, ctl_child, inherited)
              return
            end
            ctl_child.close
            spare = children[pid] = Child.new(pid, ctl, nil, false)
            collect_for_next_fork(state)
          end
        end
        if state.disabled && spare
          spare.retired = true
          spare.ctl.write("Q") rescue nil
          spare = nil
        end
        watch = [wake_r] + children.values.filter_map(&:ctl)
        watch += children.values.select(&:running).filter_map(&:conn)
        watch << server if state.disabled
        watch << state.watch.io if state.watch&.io && !exiting # stays readable once stale
        timeout = [1.0, last_activity + IDLE_TIMEOUT_S - now].max
        # Its next step, once the spare is replaced.
        timeout = 0 if state.learning && !children.values.any?(&:conn)
        timeout = 0.05 if exiting
        readable, = IO.select(watch, nil, nil, timeout)
        readable ||= []
        if state.watch&.io && readable.include?(state.watch.io) && !exiting
          stop[0] ||= "stale: a preloaded file changed"
          next
        end
        on_ctl = lambda do |child|
          kind, payload, ios = begin
            read_ctl(child.ctl)
          rescue IOError, EOFError, SystemCallError
            [nil, "", []]
          end
          if kind == "L" && state.levels.include?("2")
            data = JSON.parse(payload) rescue {}
            Array(data["modules"]).grep(String).each do |f|
              state.pending[f] = true unless state.blacklist.key?(f) || state.learned.include?(f)
            end
            Array(data["gems"]).each do |g|
              state.pending_gems[g] = true if g.is_a?(Array) && g.size == 2
            end
          end
          child.running = kind == "R" if %w[R E].include?(kind)
          if kind == "A" && !ios.empty?
            child.conn = ios.shift
            last_activity = now
            if child.equal?(spare)
              spare = nil
              spare_failures = 0
            end
          end
          ios.each(&:close)
          if kind.nil?
            child.ctl.close
            child.ctl = nil
          end
        end
        children.each_value { |c| on_ctl.call(c) if c.ctl && readable.include?(c.ctl) }
        # The client sends nothing after its go: readable means end of file.
        # Killed (kill -9): the tool dies with it, as natively. An E already
        # sent means the client left normally.
        children.each_value do |c|
          next unless c.running && readable.include?(c.conn)

          on_ctl.call(c) while c.ctl && IO.select([c.ctl], nil, nil, 0)
          next unless c.running

          c.running = false
          begin
            Process.kill("KILL", c.pid)
          rescue SystemCallError
            nil
          end
        end
        if readable.include?(wake_r)
          wake_r.read_nonblock(512, exception: false)
          loop do
            pid, status = begin
              Process.wait2(-1, Process::WNOHANG)
            rescue Errno::ECHILD
              nil
            end
            break unless pid
            child = children.delete(pid) or next
            # What it sent before dying, without waiting: a process it
            # forked may still hold its end.
            on_ctl.call(child) while child.ctl && IO.select([child.ctl], nil, nil, 0)
            if child.equal?(spare)
              spare = nil
              spare_failures += 1
              if spare_failures >= 3
                state.disabled = "spare children keep dying"
                log(state.disabled)
              end
            end
            if child.conn
              begin
                send_msg(child.conn, "W", wait_status_payload(status))
              rescue IOError, SystemCallError
                nil # client gone
              end
            end
            child.close
          end
        end
        if readable.include?(server)
          conn = server.accept_nonblock(exception: false)
          if conn != :wait_readable
            last_activity = now
            stop[0] ||= "stop request" unless handle_inline(state, conn)
          end
        end
        active = children.values.any?(&:conn)
        if (state.pending.any? || state.pending_gems.any? || state.learning) && !state.disabled && !active
          changed, why = apply_learned(state)
          if why
            stop[0] ||= "learned requires misbehaved: #{why}"
          elsif changed
            if spare # replace it: it was forked before this
              spare.retired = true
              spare.ctl.write("Q") rescue nil
              spare = nil
            end
          end
        end
        stop[0] ||= "idle timeout" if !active && now >= last_activity + IDLE_TIMEOUT_S
      end
    ensure
      if Process.pid == PARENT_PID && !lock.closed?
        mark_exiting(state)
        unlink_socket(state)
        lock.close
      end
    end
  end

  # As the daemon exits, the tool's at_exit handlers are for what it made as it
  # loaded, which they remove. One that saves what it loaded would overwrite
  # what the runs saved.
  def self.write_nothing
    refuse = ->(path) { raise Errno::EROFS, "the daemon writes nothing as it exits: #{path}" }
    File.prepend(Module.new do
      define_method(:initialize) do |path, mode = "r", *rest, **kw|
        flags = kw[:mode] || mode
        writing = flags.is_a?(Integer) ? flags & (File::WRONLY | File::RDWR | File::CREAT | File::TRUNC) != 0 : flags.to_s.match?(/[wa+]/)
        refuse.call(path) if writing
        super(path, mode, *rest, **kw)
      end
    end)
    [IO, File].each do |kind|
      kind.singleton_class.prepend(Module.new do
        %i[write binwrite].each { |name| define_method(name) { |path, *_rest, **_kw| refuse.call(path) } }
      end)
    end
  end

  def self.parse_args(argv)
    opts = {}
    it = argv.each
    loop do
      flag = it.next
      unless %w[--tool --script --socket --lock-fd --origin --tty].include?(flag)
        abort "speedrunner_daemon: unknown argument #{flag.inspect}"
      end
      opts[flag[2..]] = it.next
    end
    missing = %w[tool script socket] - opts.keys
    abort "speedrunner_daemon: missing #{missing.join(', ')}" unless missing.empty?
    opts
  end
end

at_exit { Speedrunner.report_exit } # before any require can register its own
opts = Speedrunner.parse_args(ARGV)
state = Speedrunner::State.new(opts["tool"], opts["script"], opts["socket"])
state.lock_fd = Integer(opts["lock-fd"]) if opts["lock-fd"]
state.origin = opts["origin"] if opts["origin"]
state.tty = Speedrunner.tty_stand_in(opts["tty"])
ARGV.clear
begin
  Speedrunner.serve(state)
ensure
  Speedrunner.write_nothing if Process.pid == Speedrunner::PARENT_PID
end
