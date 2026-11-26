#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a synthetic draft for checking two transition cases in render.py:
a Mix between scaled and moved clips on an upper video track, and a Mix
between base-track clips that carry a local .cube LUT.

The source draft is only read. The test draft goes to a new folder that
render.py can export; CapCut itself never opens it.

Usage:
  python buildTransitionTestDraft.py "<source draft folder>" "<test folder>" [--scale 0.45]
"""
import argparse, copy, json, os, shutil

ap = argparse.ArgumentParser()
ap.add_argument("source")
ap.add_argument("target")
ap.add_argument("--scale", type=float, default=0.45, help="upper clip scale")
ap.add_argument("--x", type=float, default=0.45, help="upper clip x offset")
ap.add_argument("--y", type=float, default=0.4, help="upper clip y offset")
args = ap.parse_args()

shutil.rmtree(args.target, ignore_errors=True)
os.makedirs(args.target)
with open(os.path.join(args.source, "draft_content.json"), encoding="utf-8") as file:
    draft = json.load(file)
draft["tracks"] = [t for t in draft["tracks"] if t["type"] in ("video", "audio")]
base = draft["tracks"][0]["segments"]
if len(base) < 4:
    raise SystemExit("the first video track needs at least four clips")
materials = draft["materials"]

# A strong red tint, so the grade is easy to see on both sides of a cut.
lut = os.path.join(os.path.abspath(args.target), "redTint.cube")
with open(lut, "w", encoding="ascii") as file:
    file.write("LUT_3D_SIZE 2\n")
    for b in (0, 1):
        for g in (0, 1):
            for r in (0, 1):
                file.write(f"{min(1, r * 0.6 + 0.4):.3f} {g * 0.6:.3f} {b * 0.6:.3f}\n")
materials.setdefault("effects", []).append(
    {"id": "testLut", "name": "Test LUT", "type": "filter", "path": lut, "value": 1.0})
for segment in base[:2]:
    segment["extra_material_refs"].append("testLut")

materials.setdefault("transitions", []).append(
    {"id": "testMix", "name": "Mix", "duration": 1_000_000, "resource_id": "6724845717472416269"})


def upper(source, start, refs):
    segment = copy.deepcopy(source)
    segment.update(id=f"upper{start}", extra_material_refs=refs, common_keyframes=[],
                   target_timerange={"start": start, "duration": 2_000_000},
                   source_timerange={"start": 0, "duration": 2_000_000},
                   track_render_index=1, volume=0.0)
    segment["clip"] = {"alpha": 1.0, "rotation": 0.0,
                       "flip": {"horizontal": False, "vertical": False},
                       "scale": {"x": args.scale, "y": args.scale},
                       "transform": {"x": args.x, "y": args.y}}
    return segment


# Two adjacent clips from 5 s to 9 s with a 1 s Mix centred on 7 s.
draft["tracks"].append({"id": "upperTrack", "type": "video", "attribute": 0, "flag": 0,
                        "segments": [upper(base[2], 5_000_000, ["testMix"]),
                                     upper(base[3], 7_000_000, [])]})
with open(os.path.join(args.target, "draft_content.json"), "w", encoding="utf-8") as file:
    json.dump(draft, file, ensure_ascii=False)
print(args.target)
