# Polymarket BTC Binary Options — Analysis Commands
#
# Usage:
#   make list                    — show all complete windows
#   make plot w=1773963000       — matplotlib PNG for one window
#   make plot-all                — matplotlib PNGs for every window
#   make interactive w=1773963000 — plotly HTML for one window
#   make dashboard               — multi-window plotly dashboard
#   make iv-overlay              — IV curves across all windows
#   make iv-summary              — IV box plot across all windows
#   make all                     — generate everything
#
# Output goes to ./output/

PYTHON  := python -m analysis.run
DB      := collector.db

# ── Single window ──────────────────────────────────────────────

list:
	$(PYTHON) --db $(DB) list

plot:
ifndef w
	@echo "Usage: make plot w=<window_ts>"
	@echo "  e.g. make plot w=1773963000"
	@echo "  Or use 'make plot-all' for every window"
	@exit 1
endif
	$(PYTHON) --db $(DB) plot -w $(w)

interactive:
ifndef w
	@echo "Usage: make interactive w=<window_ts>"
	@exit 1
endif
	$(PYTHON) --db $(DB) interactive -w $(w)

# ── All windows ────────────────────────────────────────────────

plot-all:
	$(PYTHON) --db $(DB) plot --all

dashboard:
	$(PYTHON) --db $(DB) dashboard

iv-overlay:
	$(PYTHON) --db $(DB) iv-overlay

iv-summary:
	$(PYTHON) --db $(DB) iv-summary

# ── Everything ─────────────────────────────────────────────────

stats:
	$(PYTHON) --db $(DB) stats

all: plot-all dashboard iv-overlay iv-summary stats
	@echo "All outputs generated in ./output/"

# ── Utility ────────────────────────────────────────────────────

clean:
	rm -rf output/

.PHONY: list plot plot-all interactive dashboard iv-overlay iv-summary all clean