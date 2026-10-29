<?php

namespace Phptest;

final class Cli
{
    /** @var resource[] */
    public static array $keep = [];

    public static function main(array $argv): int
    {
        $name = $argv[1] ?? '';
        $method = 'cmd' . str_replace('-', '', ucwords($name, '-'));
        if ($name === '' || !method_exists(self::class, $method)) {
            fwrite(STDERR, "usage: phptest <command> ...\n");
            return 2;
        }
        return self::$method(array_slice($argv, 2)) ?? 0;
    }

    public static function cmdEchoStdin(array $args): ?int
    {
        stream_copy_to_stream(STDIN, STDOUT);
        return null;
    }

    public static function cmdExit(array $args): ?int
    {
        $kind = $args[0];
        if ($kind === 'noarg') {
            exit;
        }
        if (str_starts_with($kind, 'int:')) {
            exit((int) substr($kind, 4));
        }
        if (str_starts_with($kind, 'str:')) {
            exit(substr($kind, 4));
        }
        if ($kind === 'throw') {
            Failing::run();
        }
        if ($kind === 'fatal') {
            phptest_no_such_function();
        }
        return (int) $kind;
    }

    public static function cmdEnv(array $args): ?int
    {
        foreach ($args as $name) {
            $sub = shell_exec('printf %s "${' . $name . '-<unset>}"');
            echo json_encode(['name' => $name, 'getenv' => getenv($name) === false ? null : getenv($name),
                              'server' => $_SERVER[$name] ?? null, 'subprocess' => $sub]), "\n";
        }
        return null;
    }

    public static function cmdServer(array $args): ?int
    {
        $env = getenv();
        $keys = array_values(array_filter(array_keys($_SERVER), fn ($k) => !array_key_exists($k, $env)));
        echo json_encode(['keys' => $keys, 'PHP_SELF' => $_SERVER['PHP_SELF'],
                          'SCRIPT_NAME' => $_SERVER['SCRIPT_NAME'], 'SCRIPT_FILENAME' => $_SERVER['SCRIPT_FILENAME'],
                          'argc' => $_SERVER['argc'], 'env_keys_match' => array_keys(array_intersect_key($_SERVER, $env)) === array_keys($env),
                          'request_time_fresh' => abs($_SERVER['REQUEST_TIME'] - time()) <= 2,
                          'request_time_float_fresh' => abs($_SERVER['REQUEST_TIME_FLOAT'] - microtime(true)) < 2,
                          // Without a php.ini, variables_order fills $_ENV: the test
                          // run's own SPEEDRUNNER_* variables are no part of it.
                          'ENV' => array_filter($_ENV, fn ($k) => !str_starts_with($k, 'SPEEDRUNNER_'),
                                                ARRAY_FILTER_USE_KEY)]), "\n";
        return null;
    }

    public static function cmdCwd(array $args): ?int
    {
        echo getcwd(), "\n", getenv('PWD'), "\n";
        return null;
    }

    public static function cmdUmask(array $args): ?int
    {
        printf("0%o\n", umask());
        return null;
    }

    public static function cmdRandom(array $args): ?int
    {
        echo bin2hex(random_bytes(16)), "\n", mt_rand(), "\n", rand(), "\n";
        return null;
    }

    public static function cmdTty(array $args): ?int
    {
        foreach (['stdin' => STDIN, 'stdout' => STDOUT, 'stderr' => STDERR] as $name => $s) {
            echo json_encode(['stream' => $name, 'tty' => stream_isatty($s), 'posix' => posix_isatty($s)]), "\n";
        }
        return null;
    }

    public static function cmdInterleave(array $args): ?int
    {
        for ($i = 0; $i < 3; $i++) {
            echo "out$i\n";
            fwrite(STDERR, "err$i\n");
        }
        return null;
    }

    public static function cmdSleep(array $args): ?int
    {
        echo "ready\n";
        sleep((int) $args[0]);
        echo "done\n";
        return null;
    }

    public static function cmdBinary(array $args): ?int
    {
        echo str_repeat(implode('', array_map('chr', range(0, 255))), 4);
        return null;
    }

    public static function cmdUnclosed(array $args): ?int
    {
        $f = fopen($args[0], 'w');
        fwrite($f, str_repeat('x', 100000));
        self::$keep[] = $f;
        return null;
    }

    public static function cmdAtexit(array $args): ?int
    {
        register_shutdown_function(function () {
            echo "bye\n";
        });
        echo "hi\n";
        return null;
    }

    public static function cmdArgv(array $args): ?int
    {
        var_export($GLOBALS['argv']);
        echo "\n", $GLOBALS['argc'], ' ', basename(__FILE__), ' ', get_included_files() === array_unique(get_included_files()) ? 'ok' : 'dup', "\n";
        return null;
    }

    public static function cmdSignals(array $args): ?int
    {
        foreach (['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT', 'SIGPIPE', 'SIGCHLD', 'SIGUSR1'] as $sig) {
            echo $sig, ' ', var_export(pcntl_signal_get_handler(constant($sig)), true), "\n";
        }
        return null;
    }

    public static function cmdHold(array $args): ?int
    {
        [$seconds, $marker] = $args;
        file_put_contents($marker, "start\n", FILE_APPEND);
        echo 'ready ', getmypid(), "\n";
        usleep((int) ((float) $seconds * 1e6));
        file_put_contents($marker, "done\n", FILE_APPEND);
        echo "done\n";
        return null;
    }

    public static function cmdPrompt(array $args): ?int
    {
        echo 'Name: ';
        $name = trim(fgets(STDIN));
        echo 'Secret: ';
        shell_exec('stty -echo');
        $secret = trim(fgets(STDIN));
        shell_exec('stty echo');
        echo "\nname=$name secret_len=", strlen($secret), "\n";
        return null;
    }

    public static function cmdGlobals(array $args): ?int
    {
        // The script's top-level variables must be globals, as natively.
        echo isset($GLOBALS['phptest_top']) ? 'global' : 'local', "\n";
        return null;
    }
}
