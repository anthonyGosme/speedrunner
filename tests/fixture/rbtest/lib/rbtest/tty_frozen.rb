# frozen_string_literal: true

# Notes at load whether the streams are terminals, as the rainbow gem does
# for rubocop: the daemon's state must not stand for the client's.
module RbtestTty
  FROZEN = [$stdin.tty?, $stdout.isatty, STDERR.tty?].freeze
end
