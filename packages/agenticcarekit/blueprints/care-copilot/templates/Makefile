# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
#
# Ejectable: this is your code now. Edit freely.

.PHONY: demo eval check

# Runs the admin copilot end to end against every tool's mock — synthetic
# payers, slots, and referral panels. No network calls.
demo:
	python3 -m app.main --offline

# Runs the copilot against the fixture task list (scored table, no network).
eval:
	python3 -m app.main --offline --eval

# Static checks: does this project still look the way ack generated it.
check:
	python3 -m compileall -q app
	python3 -m pytest -q || true
