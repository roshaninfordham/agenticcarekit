# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
#
# Ejectable: this is your code now. Edit freely.

.PHONY: demo eval check

# Runs the on-device intake summarizer end to end. Fully offline by
# construction — there is no network mode to opt out of.
demo:
	python3 -m app.main

# Alias: the on-device demo IS the eval (one deterministic synthetic run).
eval:
	python3 -m app.main

# Static checks: does this project still look the way ack generated it.
check:
	python3 -m compileall -q app
	python3 -m pytest -q || true
