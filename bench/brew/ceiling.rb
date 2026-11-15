# Ceiling of a preloaded fork for one brew command, without a client.
# Run with brew's own Ruby, flags and environment (see ceiling.py):
#   ruby -W1 --disable=gems,rubyopt ceiling.rb <runs> <out_dir> <brew args...>
# 1. a child runs the command once and reports the files it loaded, in the
#    order their loading started (as the Ruby daemon learns, level 2);
# 2. this process requires them, then forks for each run of the command.
require "json"

BREW_RB = "/usr/local/Homebrew/Library/Homebrew/brew.rb"
runs = Integer(ARGV.shift)
out = ARGV.shift
cmd = ARGV.dup
ARGV.clear

def run_brew(cmd, stdout_path)
  $stdout.reopen(stdout_path, "w")
  $stderr.reopen(File::NULL, "w")
  $PROGRAM_NAME = BREW_RB
  ARGV.replace(cmd)
  load BREW_RB
end

def now = Process.clock_gettime(Process::CLOCK_MONOTONIC)

# 1. Learn.
r, w = IO.pipe
learner = fork do
  r.close
  order = []
  TracePoint.new(:script_compiled) do |t|
    order << t.instruction_sequence.path if t.eval_script.nil?
  end.enable
  at_exit { w.write(JSON.generate(order)); w.close } # registered first: runs last
  run_brew(cmd, File.join(out, "learn.out"))
end
w.close
order = JSON.parse(r.read)
Process.wait(learner)
order = order.uniq.reject { |f| f == BREW_RB || !f.start_with?("/") }

# 2. Preload, as the daemon would: global first (it sets $LOAD_PATH), then the rest.
t0 = now
$PROGRAM_NAME = BREW_RB
failed = []
saved = $stdout.dup
$stdout.reopen(File::NULL, "w")
order.each do |f|
  next if $LOADED_FEATURES.include?(f)
  begin
    require f
  rescue Exception => e # rubocop:disable Lint/RescueException
    failed << "#{f}: #{e.class}"
  end
end
$stdout.reopen(saved)
preload_ms = (now - t0) * 1000
threads = Thread.list.size
rss_mb = `ps -o rss= -p #{Process.pid}`.to_i / 1024

# 3. Fork per run.
GC.start
GC.compact if GC.respond_to?(:compact)
times = []
to_x = []
statuses = []
runs.times do |i|
  rx, wx = IO.pipe
  t = now
  pid = fork do
    rx.close
    # Registered before brew's own handlers: runs after them, where the
    # Ruby daemon sends X (the exit code) to the client.
    at_exit { $stdout.flush; $stderr.flush; wx.write([now].pack("E")); wx.close }
    run_brew(cmd, File.join(out, "fork-#{i}.out"))
  end
  wx.close
  x = rx.read
  Process.wait(pid)
  times << (now - t) * 1000
  to_x << (x.unpack1("E") - t) * 1000 if x.bytesize == 8
  statuses << $?.exitstatus
end
times.sort!
to_x.sort!
puts JSON.generate(
  "files" => order.size, "preload_ms" => preload_ms.round, "failed" => failed, "threads" => threads, "rss_mb" => rss_mb,
  "median_ms" => times[times.size / 2].round(1), "to_x_ms" => to_x[to_x.size / 2]&.round(1), "min_ms" => times.first.round(1), "statuses" => statuses.uniq,
)
