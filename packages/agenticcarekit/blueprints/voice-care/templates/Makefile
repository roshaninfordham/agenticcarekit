# Decision support only — not diagnosis, not treatment. Synthetic/public data only.
#
# This Makefile is yours now (ejectable: this is your code now). Edit it
# freely — nothing in agenticcarekit reads it back.

.PHONY: demo eval check

# Runs the voice intake loop fully offline against mock ASR/TTS and the
# synthetic sample transcripts in app/fixtures/. No network calls.
demo:
	python -m app.main --offline

# Runs the scribe/extraction eval set (scored table, no network).
eval:
	python -m app.main --offline --eval

# Static checks: does this project still look the way ack generated it.
check:
	python -m compileall -q app
	python -m pytest -q || true
