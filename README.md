# Fastmail Tools

Small scripts and tools for interacting with Fastmail via the JMAP API.

Written in Python using [jmapc](https://github.com/smkent/jmapc).

I found it hard to find examples of some of the interactions I was interested (sorting emails) so hopefully other folks find these useful.


## sort-emails-by-alias

If you are using a alias@some.example.com pattern (where you've set up a wildcard domain and treat the name before the @ as an alias) then you can sort your emails into folders matching the sieve filter in [this reddit post](https://www.reddit.com/r/fastmail/comments/pjr3u8/help_sieve_how_to_dynamically_filtersort_emails/).

Assumes you are using the pattern `To/{domain}/{alias}` as your folder structure.

Example usage (won't sort any emails with `to: me@example.com`):

```sh
fastmail-tools sort-emails-by-alias me@example.com [other emails to ignore ...]
```
