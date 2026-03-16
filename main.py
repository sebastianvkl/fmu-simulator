import os
import uuid
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import fmpy
from fmpy import read_model_description, extract, supported_platforms, platform
from fmpy.simulation import simulate_fmu

FMI_HEADERS = Path(fmpy.__file__).parent / "c-code"

app = FastAPI(title="FMU Simulator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "fmu_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)



@app.get("/", response_class=HTMLResponse)
async def root():
    with open(Path(__file__).parent / "index.html") as f:
        return f.read()


@app.post("/inspect")
async def inspect_fmu(file: UploadFile = File(...)):
    """Upload FMU and return its model description (variables, etc.)"""
    if not file.filename.endswith(".fmu"):
        raise HTTPException(400, "File must be a .fmu file")

    session_id = str(uuid.uuid4())
    fmu_path = UPLOAD_DIR / f"{session_id}.fmu"

    try:
        with open(fmu_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        model_desc = read_model_description(str(fmu_path))

        variables = []
        for var in model_desc.modelVariables:
            var_info = {
                "name": var.name,
                "description": var.description or "",
                "causality": var.causality,
                "variability": var.variability,
                "type": type(var).__name__.replace("ModelVariable", ""),
            }
            # Get start value (fmpy exposes .start and .type directly on ModelVariable)
            var_info["start"] = var.start if var.start is not None else None
            var_info["type"]  = var.type or ""
            var_info["unit"]  = var.unit or ""
            variables.append(var_info)

        # Get default experiment settings
        de = model_desc.defaultExperiment
        default_start = de.startTime if de and de.startTime is not None else 0.0
        default_stop = de.stopTime if de and de.stopTime is not None else 10.0
        default_step = de.stepSize if de and de.stepSize is not None else None

        if model_desc.coSimulation is not None:
            fmi_type = "CoSimulation"
        elif model_desc.modelExchange is not None:
            fmi_type = "ModelExchange"
        elif getattr(model_desc, "scheduledExecution", None) is not None:
            fmi_type = "ScheduledExecution"
        else:
            fmi_type = "Unknown"

        return {
            "session_id": session_id,
            "model_name": model_desc.modelName,
            "description": model_desc.description or "",
            "fmi_version": model_desc.fmiVersion,
            "fmi_type": fmi_type,
            "variables": variables,
            "default_start": default_start,
            "default_stop": default_stop,
            "default_step": default_step,
        }
    except Exception as e:
        if fmu_path.exists():
            fmu_path.unlink()
        raise HTTPException(500, f"Failed to read FMU: {str(e)}")


def _binary_is_arm64(fmu_path: str) -> bool:
    """Check whether the darwin64 binary inside the FMU is actually arm64."""
    import zipfile, struct
    try:
        with zipfile.ZipFile(fmu_path, "r") as z:
            dylibs = [n for n in z.namelist() if n.startswith(f"binaries/{platform}/") and n.endswith(".dylib")]
            if not dylibs:
                return False
            data = z.read(dylibs[0])[:8]
            magic, cputype = struct.unpack("<II", data)
            # 0x0100000C = ARM64, 0x01000007 = x86_64
            return cputype == 0x0100000C
    except Exception:
        return False


def _try_compile_from_source(fmu_path: str) -> bool:
    """
    If the FMU has C source code but no arm64-compatible binary, compile one.
    Returns True if a binary is (or was) successfully available.
    """
    import zipfile

    platforms = supported_platforms(fmu_path)

    # Binary exists and is the right architecture — nothing to do
    if platform in platforms and _binary_is_arm64(fmu_path):
        return True

    if "c-code" not in platforms:
        return False  # no source to compile

    try:
        with zipfile.ZipFile(fmu_path, "r") as z:
            names = z.namelist()
            src_files = [n for n in names if n.startswith("sources/") and n.endswith(".c")]
            if not src_files:
                return False

            tmpdir = tempfile.mkdtemp()
            z.extractall(tmpdir)

        # Detect FMI version from modelDescription.xml
        import xml.etree.ElementTree as ET
        tree = ET.parse(os.path.join(tmpdir, "modelDescription.xml"))
        fmi_version = tree.getroot().attrib.get("fmiVersion", "2.0")[0]

        model_name = tree.getroot().attrib.get("modelName", "model")
        src_dir = os.path.join(tmpdir, "sources")
        bin_dir = os.path.join(tmpdir, "binaries", platform)
        os.makedirs(bin_dir, exist_ok=True)
        out_lib = os.path.join(bin_dir, f"{model_name}.dylib")

        # Try all.c first, then individual .c files
        all_c = os.path.join(src_dir, "all.c")
        src_input = all_c if os.path.exists(all_c) else " ".join(
            os.path.join(src_dir, f) for f in os.listdir(src_dir) if f.endswith(".c")
        )

        cmd = (
            f"clang -arch arm64 -shared -fPIC -o {out_lib} {src_input} "
            f"-I{src_dir} -I{FMI_HEADERS} -DFMI_VERSION={fmi_version} -DFMI_COSIMULATION"
        )
        result = subprocess.run(cmd, shell=True, capture_output=True)
        if result.returncode != 0:
            return False

        # Repack FMU with the new binary
        with zipfile.ZipFile(fmu_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for root, dirs, files in os.walk(tmpdir):
                for file in files:
                    fpath = os.path.join(root, file)
                    arcname = os.path.relpath(fpath, tmpdir)
                    zout.write(fpath, arcname)

        return True
    except Exception:
        return False


@app.post("/simulate")
async def simulate(
    session_id: str = Form(...),
    start_time: float = Form(0.0),
    stop_time: float = Form(10.0),
    step_size: Optional[float] = Form(None),
    output_variables: str = Form(""),  # comma-separated
    start_values: str = Form("{}"),    # JSON dict of {varName: value}
):
    """Run FMU simulation and return results as JSON."""
    fmu_path = UPLOAD_DIR / f"{session_id}.fmu"
    if not fmu_path.exists():
        raise HTTPException(404, "Session not found. Please re-upload the FMU file.")

    try:
        # Auto-compile from source if no compatible binary exists
        _try_compile_from_source(str(fmu_path))

        # Auto-detect supported FMI type (CoSimulation preferred, then ModelExchange, then ScheduledExecution)
        model_desc = read_model_description(str(fmu_path))
        if model_desc.coSimulation is not None:
            fmi_type = "CoSimulation"
        elif model_desc.modelExchange is not None:
            fmi_type = "ModelExchange"
        elif getattr(model_desc, "scheduledExecution", None) is not None:
            fmi_type = "ScheduledExecution"
        else:
            raise ValueError("FMU type could not be determined from modelDescription.xml.")

        output_vars = [v.strip() for v in output_variables.split(",") if v.strip()] or None

        # Parse optional start values override
        sv = {}
        try:
            raw = json.loads(start_values) if start_values else {}
            for k, v in raw.items():
                if isinstance(v, bool):
                    sv[k] = v
                elif isinstance(v, (int, float)):
                    sv[k] = v
                else:
                    s = str(v).strip()
                    if s.lower() == "true":
                        sv[k] = True
                    elif s.lower() == "false":
                        sv[k] = False
                    else:
                        sv[k] = float(s) if ("." in s or "e" in s.lower()) else int(s)
        except Exception:
            sv = {}

        result = simulate_fmu(
            str(fmu_path),
            start_time=start_time,
            stop_time=stop_time,
            output_interval=step_size,
            output=output_vars,
            fmi_type=fmi_type,
            start_values=sv or {},
            fmi_call_logger=None,
        )

        # Convert numpy structured array to dict of lists
        time = result["time"].tolist()
        series = {}
        for name in result.dtype.names:
            if name == "time":
                continue
            vals = result[name]
            if np.issubdtype(vals.dtype, np.floating) or np.issubdtype(vals.dtype, np.integer):
                series[name] = vals.tolist()

        return {"time": time, "series": series}

    except Exception as e:
        msg = str(e)
        if "incompatible architecture" in msg or "mach-o file" in msg:
            msg = (
                "This FMU contains only an x86_64 binary and cannot run on Apple Silicon. "
                "If the FMU includes C source code it will be compiled automatically. "
                "Otherwise, request an arm64-compatible build from the FMU provider."
            )
        raise HTTPException(500, f"Simulation failed: {msg}")


@app.delete("/session/{session_id}")
async def cleanup_session(session_id: str):
    fmu_path = UPLOAD_DIR / f"{session_id}.fmu"
    if fmu_path.exists():
        fmu_path.unlink()
    return {"ok": True}
