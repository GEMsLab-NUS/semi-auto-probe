"""Hardware-free Tk editor timings; run from the repository root."""
import argparse
import copy
import cProfile
import io
import json
import pstats
import tempfile
import time
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_library import cards_from_template

parser=argparse.ArgumentParser()
parser.add_argument("--output",default=".runtime/flow-editor-performance/latest.json")
parser.add_argument("--copies", type=int, default=1, help="Stack independent template copies for larger graphs")
args=parser.parse_args()
if args.copies < 1:
    parser.error("--copies must be positive")
root=tk.Tk();root.withdraw()
colors=defaultdict(lambda:"#334155",text="#fff",muted="#94a3b8",surface="#111827",surface_2="#111827")
report={"template":"FET / HP6614C / Screen and Characterize"}
profile=cProfile.Profile()
with tempfile.TemporaryDirectory() as tmp,patch("semi_auto_probe.workflow_library.default_workflow_root",return_value=Path(tmp)):
    cards=cards_from_template(report["template"])
    original = list(cards)
    for index in range(1, args.copies):
        duplicates = copy.deepcopy(original)
        names = {card.card_id: f"copy{index}_{card.card_id}" for card in duplicates}
        for card in duplicates:
            card.card_id = names[card.card_id]
            card.y += index * 1600
            card.params = {key: names.get(value, value) if isinstance(value, str) else value
                           for key, value in card.params.items()}
        cards.extend(duplicates)
    print("Opening",len(cards),"nodes",flush=True)
    start=time.perf_counter()
    editor=MeasurementFlowEditor(root,cards,colors,lambda:"new",lambda:None)
    editor.window.withdraw()
    report["load_ms"]=(time.perf_counter()-start)*1000
    print("load_ms",report["load_ms"],flush=True)
    for name,operation in (
        ("redraw",editor.redraw),
        ("zoom",lambda:editor._zoom_canvas(SimpleNamespace(x=350,y=200,delta=120))),
        ("drag",lambda:editor._canvas_drag(SimpleNamespace(x=450,y=250))),
    ):
        if name=="drag":
            editor.drag_id=cards[1].card_id
        samples=[]
        for i in range(3):
            if name == "drag":
                # Move each sample; repeated identical coordinates only measure a cache hit.
                editor.drag_offset = (i * 32, i * 16)
            start=time.perf_counter();operation();samples.append((time.perf_counter()-start)*1000)
        report[name+"_ms"]=samples
        print(name,samples,flush=True)
    editor.drag_id=None
    profile.enable();editor.redraw();profile.disable()
    report["nodes"]=len(cards)
    report["edges"]=len(editor._routed_edges)
    editor.close()
root.destroy()
out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(report,indent=2),encoding="utf-8")
profile.dump_stats(str(out.with_suffix(".prof")))
stream=io.StringIO();pstats.Stats(profile,stream=stream).sort_stats("cumulative").print_stats(18)
print(stream.getvalue(),flush=True)
