Gem::Specification.new do |s|
  s.name = "rbtest"
  s.version = "0.1.0"
  s.summary = "Fixture CLI for the speedrunner regression matrix (Ruby)"
  s.authors = ["speedrunner"]
  s.files = Dir["lib/**/*.rb"] + Dir["exe/*"]
  s.bindir = "exe"
  s.executables = ["rbtest", "rbrun", "rbtty", "rbthread"]
  s.license = "MIT"
end
