Interpreter boot: 6.6 ms bare, 40.0 ms with RubyGems

| Command | Native | Boot + require of the library | Share |
|---|---|---|---|
| `rubocop --version` | 414 ms | 352 ms | 85% |
| `sample.rb` | 730 ms | 357 ms | 49% |
| `rake --version` | 79 ms | 62 ms | 78% |
| `bundle --version` | 96 ms | 70 ms | 73% |
| `gem --version` | 56 ms | 36 ms | 65% |
