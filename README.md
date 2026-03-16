# FMU Simulator

A web-based simulator for FMI 2.0 / 3.0 models (.fmu files). Upload any FMU, configure parameters, run simulations, and explore results interactively.

![FMU Simulator screenshot](https://github.com/sebastianvkl/fmu-simulator/assets/screenshot.png)

## Features

- **FMU upload & inspect** — reads model description, variables, default experiment settings
- **Parameter editor** — override initial values for parameters and inputs before each run
- **Multi-run comparison** — up to 5 runs overlaid on the same charts with distinct colors
- **Animated playback** — scrub through simulation results with play/pause, speed control, and keyboard shortcuts (`Space`, `R`, `←`, `→`)
- **Three views:**
  - **Charts** — one Plotly chart per variable with per-chart stats (min / mean / max)
  - **Phase portrait** — parametric X/Y plot with variable selectors
  - **3D trajectory** — Three.js 3D plot with X/Y/Z axis mapping
- **CSV export** — download the active run as a CSV file
- **Apple Silicon support** — auto-compiles FMUs from C source when no arm64 binary is included

## Requirements

- Python 3.10+
- [fmpy](https://github.com/CATIA-Systems/FMPy) ≥ 0.3.28
- [FastAPI](https://fastapi.tiangolo.com/) ≥ 0.110
- [uvicorn](https://www.uvicorn.org/)

```
pip install fmpy fastapi uvicorn python-multipart numpy
```

## Running

```bash
uvicorn main:app --reload --port 8765
```

Then open [http://localhost:8765](http://localhost:8765).

## Usage

1. **Load FMU** — drag & drop or click to browse for a `.fmu` file
2. **Configure** — set start/stop time, step size, and select output variables; optionally override parameter values in the **Initial Values** panel
3. **Run** — click **Run Simulation**; results appear immediately
4. **Explore** — use the playback controls to animate results, switch between Charts / Phase / 3D views, or export to CSV
5. **Compare** — run again with different parameters to overlay results

## Supported FMU types

| Type | Support |
|------|---------|
| CoSimulation | ✅ |
| ModelExchange | ✅ |
| ScheduledExecution | ✅ |
| FMI 1.0 | ✅ |
| FMI 2.0 | ✅ |
| FMI 3.0 | ✅ |
