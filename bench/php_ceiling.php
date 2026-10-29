<?php
// Estimate the warm-path ceiling of fork-based preloading for a PHP CLI.
//
// A parent forks children that run the tool's script (include, as the
// interpreter would run it) and times fork -> child reaped, per level:
//
//   none     nothing preloaded beyond the interpreter: the interpreter
//            start and the extensions of php.ini are saved, nothing else
//   opcache  OPcache on (shared memory, created by the parent): the files
//            a first child compiles are cached for the next ones, as for
//            PHP-FPM workers. The parent compiles nothing itself:
//            opcache_compile_file() early-binds simple classes into the
//            parent, and a later plain `require` of the file (Composer's
//            autoload_static.php) is then a fatal redeclaration. For the
//            same reason classes cannot be declared in the parent.
//
// Children skip teardown (SIGKILL from the last shutdown function, after
// flushing), as the daemon would by reporting the exit code first. No
// client, socket or fd passing: an upper bound, not the product.
// Mirrors bench/preload_ceiling.py and bench/ruby_ceiling.rb.
//
// Usage: php -d opcache.enable_cli=1 php_ceiling.php <level> <runs> <out.json> <script> -- <args>...

[, $level, $runs, $out, $script, $sep] = $argv;
$args = array_slice($argv, 6);
if ($sep !== '--') {
    fwrite(STDERR, "usage: see header\n");
    exit(2);
}
$runs = (int) $runs;
const WARMUP = 3;

function rss_mb(): float
{
    preg_match('/VmRSS:\s+(\d+)/', file_get_contents('/proc/self/status'), $m);
    return $m[1] / 1024;
}

// Shutdown functions run in registration order; one registered from inside
// a shutdown function runs after all the others: we re-register once.
function hs_last(): void
{
    static $again = true;
    if ($again) {
        $again = false;
        register_shutdown_function('hs_last');
        return;
    }
    while (ob_get_level() > 0) {
        ob_end_flush();
    }
    $report = $GLOBALS['__hs_report'];
    $data = ['files' => $report ? get_included_files() : []];
    fwrite($GLOBALS['__hs_sock'], json_encode($data));
    fclose($GLOBALS['__hs_sock']);
    posix_kill(getmypid(), SIGKILL);
}

function run_child(string $script, array $args, bool $report): array
{
    $pair = stream_socket_pair(STREAM_PF_UNIX, STREAM_SOCK_STREAM, STREAM_IPPROTO_IP);
    $pid = pcntl_fork();
    if ($pid === 0) {
        fclose($pair[0]);
        $GLOBALS['__hs_sock'] = $pair[1];
        $GLOBALS['__hs_report'] = $report;
        register_shutdown_function('hs_last');
        $GLOBALS['argv'] = $_SERVER['argv'] = array_merge([$script], $args);
        $GLOBALS['argc'] = $_SERVER['argc'] = count($GLOBALS['argv']);
        include $script;
        exit(0);
    }
    fclose($pair[1]);
    $data = stream_get_contents($pair[0]);
    fclose($pair[0]);
    pcntl_waitpid($pid, $status);
    return json_decode($data, true) ?? [];
}

$info = ['level' => $level, 'runs' => $runs, 'rss_boot_mb' => rss_mb(),
         'opcache' => function_exists('opcache_get_status') && (opcache_get_status(false)['opcache_enabled'] ?? false)];
if ($level === 'opcache') {
    $info['learned_files'] = count(run_child($script, $args, true)['files'] ?? []);
}
gc_collect_cycles();
$info['rss_preloaded_mb'] = rss_mb();

$times = [];
for ($i = 0; $i < WARMUP + $runs; $i++) {
    $t0 = hrtime(true);
    run_child($script, $args, false);
    if ($i >= WARMUP) {
        $times[] = (hrtime(true) - $t0) / 1e9;
    }
}
sort($times);
$info['median_s'] = $times[intdiv(count($times), 2)];
$mean = array_sum($times) / count($times);
$info['stdev_s'] = sqrt(array_sum(array_map(fn ($t) => ($t - $mean) ** 2, $times)) / max(1, count($times) - 1));
file_put_contents($out, json_encode($info, JSON_PRETTY_PRINT));
fprintf(STDERR, "%-8s %-40s median %6.1f ms ± %4.1f  RSS %.0f MB\n", $level,
        basename($script) . ' ' . implode(' ', $args), $info['median_s'] * 1000, $info['stdev_s'] * 1000,
        $info['rss_preloaded_mb']);
