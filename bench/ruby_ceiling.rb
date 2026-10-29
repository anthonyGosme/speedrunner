# Estimate the warm-path ceiling of fork-based preloading for a Ruby CLI.
#
# A preloaded parent forks children that run the tool's executable (as the
# binstub would: `load exe`) and times fork -> child reaped, per level:
#
#   none     nothing preloaded beyond the interpreter and RubyGems
#   lib      + the tool's main library (e.g. require "rubocop")
#   learned  + every feature a first run loaded, in $LOADED_FEATURES order
#
# Children skip interpreter teardown (exit! after flushing), as the daemon
# would by reporting the exit code first. No client, socket or fd passing:
# an upper bound, not the product. Mirrors bench/preload_ceiling.py.
#
# Usage: ruby ruby_ceiling.rb <level> <runs> <out.json> <exe> <lib> -- <args>...
require "json"

level, runs, out, exe, lib, sep, *args = ARGV
abort "usage: see header" unless sep == "--"
runs = Integer(runs)
WARMUP = 3

def rss_mb
  File.read("/proc/self/status")[/VmRSS:\s+(\d+)/, 1].to_i / 1024.0
end

def run_child(exe, args, report = nil)
  r, w = IO.pipe
  pid = fork do
    r.close
    devnull = File.open(File::NULL, "r+")
    [$stdin, $stdout, $stderr].each { |io| io.reopen(devnull) }
    before = $LOADED_FEATURES.dup if report
    code = 0
    begin
      $0 = exe
      ARGV.replace(args)
      load exe
    rescue SystemExit => e
      code = e.status
    rescue Exception => e # rubocop:disable Lint/RescueException
      code = "#{e.class}: #{e.message[0, 200]}"
    end
    # The tool may have replaced or closed $stdout (rubocop does).
    [$stdout, $stderr, STDOUT, STDERR].each do |io|
      io.flush
    rescue IOError
      nil
    end
    w.write(JSON.generate(code: code, features: report ? $LOADED_FEATURES - before : []))
    w.close
    exit!(0)
  end
  w.close
  result = JSON.parse(r.read)
  r.close
  Process.wait(pid)
  result
end

info = { level: level, runs: runs, rss_boot_mb: rss_mb }
require lib if %w[lib learned].include?(level)
if level == "learned"
  learned = run_child(exe, args, true)["features"]
  failed = learned.reject do |f|
    require f
    true
  rescue Exception # rubocop:disable Lint/RescueException
    false
  end
  info.update(learned_features: learned.size, failed: failed)
end
GC.compact if GC.respond_to?(:compact)
info[:rss_preloaded_mb] = rss_mb

times, codes = [], []
(WARMUP + runs).times do |i|
  t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  codes << run_child(exe, args)["code"]
  times << Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0 if i >= WARMUP
end
sorted = times.sort
info[:median_s] = sorted[sorted.size / 2]
mean = times.sum / times.size
info[:stdev_s] = Math.sqrt(times.sum { |t| (t - mean)**2 } / (times.size - 1))
info[:codes] = codes.uniq
File.write(out, JSON.pretty_generate(info))
printf("%-8s %-40s median %6.1f ms ± %4.1f  codes=%s  RSS %.0f MB\n", level,
       "#{File.basename(exe)} #{args.join(' ')}", info[:median_s] * 1000,
       info[:stdev_s] * 1000, info[:codes], info[:rss_preloaded_mb])
