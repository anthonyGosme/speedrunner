# State loaded as it loads and saved at exit, as a tool with a history file.
module RbState
  PATH = File.expand_path("~/rbstate")
  class << self
    attr_accessor :value
  end
  self.value = File.exist?(PATH) ? File.read(PATH) : ""
end
at_exit { File.write(RbState::PATH, RbState.value) }
