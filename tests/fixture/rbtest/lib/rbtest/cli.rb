# rbtest: one subcommand per behaviour the regression matrix compares.
require "json"
require "securerandom"
require "rbtest"

module Rbtest
  # Its message comes from an overridden #message, with colour codes in it,
  # as CocoaPods' CLAide::Help does.
  class ColourMessage < StandardError
    def message = "\e[31m[!] You cannot do that.\e[39m\n\n\e[4mUsage:\e[24m rbtest"
  end

  module CLI
    KEEP = []

    module_function

    def cmd_echo_stdin(_args)
      $stdout.binmode
      $stdout.write($stdin.binmode.read)
    end

    def cmd_exit(args)
      kind = args[0]
      case kind
      when "noarg" then exit
      when "true" then exit(true)
      when "false" then exit(false)
      when /\Aint:(\d+)\z/ then exit(Integer($1))
      when /\Aabort:(.*)\z/ then abort($1)
      when /\Abang:(\d+)\z/ then exit!(Integer($1))
      when "raise" then raise ArgumentError, "rbtest failure"
      # As CocoaPods does: colour codes, and more, in the message.
      when "raise-escapes" then raise "\e[31m[!] red\e[39m \\ a\tb \x01\x7f é\nsecond line"
      when "raise-message" then raise Rbtest::ColourMessage
      when "kbint" then raise Interrupt
      else Integer(kind)
      end
    end

    def cmd_env(args)
      require "fiddle"
      getenv = Fiddle::Function.new(Fiddle.dlopen(nil)["getenv"], [Fiddle::TYPE_VOIDP],
                                    Fiddle::TYPE_VOIDP)
      args.each do |name|
        c = getenv.call(name)
        sub = IO.popen(["sh", "-c", "printf %s \"${#{name}-<unset>}\""], &:read)
        puts JSON.generate(name: name, ruby: ENV[name], c: c.null? ? nil : c.to_s,
                           subprocess: sub)
      end
      0
    end

    def cmd_cwd(_args)
      puts Dir.pwd
      puts ENV["PWD"]
    end

    def cmd_umask(_args)
      puts format("0o%o", File.umask)
    end

    def cmd_random(_args)
      puts SecureRandom.uuid
      puts rand
    end

    def cmd_tty(_args)
      require "io/console"
      { "stdin" => $stdin, "stdout" => $stdout, "stderr" => $stderr }.each do |name, io|
        puts JSON.generate(stream: name, tty: io.tty?, sync: io.sync,
                           encoding: io.external_encoding&.name)
      end
      p $stdout.tty? ? IO.console&.winsize : nil
    end

    def cmd_interleave(_args)
      3.times do |i|
        puts "out#{i}"
        warn "err#{i}"
      end
    end

    def cmd_sleep(args)
      puts "ready"
      $stdout.flush
      sleep Float(args[0])
      puts "done"
    end

    def cmd_binary(_args)
      $stdout.binmode
      $stdout.write((0..255).to_a.pack("C*") * 4)
    end

    def cmd_unclosed(args)
      f = File.open(args[0], "w")
      f.write("x" * 100_000)
      KEEP << f
    end

    def cmd_atexit(_args)
      at_exit { puts "bye" }
      puts "hi"
    end

    def cmd_argv(_args)
      p $0
      p ARGV
      p $PROGRAM_NAME == $0
      p $LOAD_PATH.include?(File.expand_path("..", __dir__))
    end

    def cmd_signals(_args)
      %w[INT TERM HUP QUIT PIPE CHLD USR1].each do |sig|
        handler = trap(sig, "SYSTEM_DEFAULT")
        trap(sig, handler)
        puts "#{sig} #{handler.inspect}"
      end
    end

    def cmd_hold(args)
      seconds, marker = Float(args[0]), args[1]
      mark = ->(step) { File.open(marker, "a") { |f| f.puts step } }
      mark.("start")
      puts "ready #{Process.pid}"
      $stdout.flush
      sleep seconds
      mark.("done")
      puts "done"
    end

    def cmd_prompt(_args)
      require "io/console"
      print "Name: "
      $stdout.flush
      name = $stdin.gets.chomp
      secret = IO.console.getpass("Secret: ")
      puts "name=#{name} secret_len=#{secret.size}"
    end

    def cmd_secret_then_stdin(_args)
      # As tools that read data on stdin and ask for a password on the console.
      require "io/console"
      secret = IO.console.getpass("Secret: ")
      data = $stdin.read
      puts "secret=#{secret.inspect} stdin=#{data.inspect}"
    end

    def cmd_pending_output(_args)
      # Written once the reader is gone: deterministic, unlike a race with it.
      sleep 0.3
      $stdout.write("x\n")
    end

    def cmd_lazy(_args)
      # Required on first use only: what learning picks up.
      require "rbtest/lazy_plain"
      require "rbtest/lazy_thread"
      require "rbtest/lazy_print"
      require_relative "lazy_env"
      require "rbtest/lazy_argv"
      require "rbtest/lazy_uses_argv"
      p [Rbtest::LazyPlain::VALUE, Rbtest::LazyEnv::VALUE, Rbtest::LazyArgv::VALUE]
    end

    def cmd_reopen(_args)
      require "rbtest/lazy_alias"
      require "rbtest/lazy_reopen"
      p Rbtest::Real.const_defined?(:REOPENED, false)
    end

    def cmd_frozen(_args)
      p Rbtest::FROZEN
    end

    COMMANDS = singleton_methods.grep(/\Acmd_/).to_h do |m|
      [m.to_s.delete_prefix("cmd_").tr("_", "-"), m]
    end

    def main(argv)
      name = argv[0]
      unless COMMANDS.key?(name)
        warn "usage: rbtest {#{COMMANDS.keys.sort.join(',')}} ..."
        return 2
      end
      result = public_send(COMMANDS[name], argv[1..])
      result.is_a?(Integer) ? result : 0
    end
  end
end
