"""Tests for public Krea2 + MOSS dual-sampling, MOSS Base/SFT, and SeedVR2 pack workflows."""
import json
import tempfile
import unittest
from pathlib import Path

from .workflows import WorkflowError, WorkflowStore

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
WORKFLOW_FILE = WORKFLOWS_DIR / "krea2-moss-dual.json"
MOSS_BASE_FILE = WORKFLOWS_DIR / "moss-base.json"
MOSS_SFT_FILE = WORKFLOWS_DIR / "moss-sft.json"
ANIMA_PACK_FILE = WORKFLOWS_DIR / "pack-anima-seedvr2.json"
ILLUSTRIOUS_PACK_FILE = WORKFLOWS_DIR / "pack-illustrious-seedvr2.json"


class Krea2MossPublicWorkflowTests(unittest.TestCase):
    def _load(self):
        return json.loads(WORKFLOW_FILE.read_text("utf-8"))

    def test_save_and_build_maps_dual_stage_fields_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            record = store.save(self._load())
            self.assertEqual(record["id"], "krea2-moss-dual")
            self.assertEqual(record["name"], "Krea2 Turbo + MOSS Base 双采")

            built = store.build(
                {
                    "workflow_id": "krea2-moss-dual",
                    "prompt": "style_demo, 1girl, lantern courtyard",
                    "negative_prompt": "lowres, photorealistic",
                    "seed": 123456,
                    "steps": 30,
                    "cfg": 3.2,
                    "sampler": "er_sde",
                    "scheduler": "simple",
                    "denoise": 0.6,
                    "width": 1024,
                    "height": 1536,
                    "n": 2,
                }
            )
            prompt = built["prompt"]

            # Stage 1 and Stage 2 prompts & seeds are synchronized
            self.assertEqual(prompt["8"]["inputs"]["text"], "style_demo, 1girl, lantern courtyard")
            self.assertEqual(prompt["27"]["inputs"]["text"], "style_demo, 1girl, lantern courtyard")
            self.assertEqual(prompt["28"]["inputs"]["text"], "lowres, photorealistic")
            self.assertEqual(prompt["11"]["inputs"]["seed"], 123456)
            self.assertEqual(prompt["30"]["inputs"]["seed"], 123456)

            # Stage 1 Krea2 Turbo stays locked at 8 steps, CFG 1.0, denoise 1.0
            self.assertEqual(prompt["11"]["inputs"]["steps"], 8)
            self.assertEqual(prompt["11"]["inputs"]["cfg"], 1.0)
            self.assertEqual(prompt["11"]["inputs"]["denoise"], 1.0)
            self.assertEqual(prompt["11"]["inputs"]["sampler_name"], "euler")

            # Stage 2 MOSS receives user sampling controls
            self.assertEqual(prompt["30"]["inputs"]["steps"], 30)
            self.assertEqual(prompt["30"]["inputs"]["cfg"], 3.2)
            self.assertEqual(prompt["30"]["inputs"]["sampler_name"], "er_sde")
            self.assertEqual(prompt["30"]["inputs"]["scheduler"], "simple")
            self.assertEqual(prompt["30"]["inputs"]["denoise"], 0.6)

            # Resolution and batch_size apply to EmptyLatentImage
            self.assertEqual(prompt["10"]["inputs"]["width"], 1024)
            self.assertEqual(prompt["10"]["inputs"]["height"], 1536)
            self.assertEqual(prompt["10"]["inputs"]["batch_size"], 2)

            # All 6 public core models (2 UNETs, 2 CLIPs, 2 VAEs) are captured
            req_names = [m.get("filename") or m.get("comfy_name") for m in built["required_models"]]
            expected = [
                "krea2_turbo_fp8.safetensors",
                "qwen3vl_4b_fp8_scaled.safetensors",
                "qwen_image_vae.safetensors",
                "moss_image_base_bf16.safetensors",
                "qwen3.5_9b_int8_convrot.safetensors",
                "flux2-vae.safetensors",
            ]
            self.assertEqual(len(built["required_models"]), 6)
            for fname in expected:
                self.assertIn(fname, req_names)

    def test_shift_mapping_and_validation_across_moss_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.save(self._load())
            store.save(json.loads(MOSS_BASE_FILE.read_text("utf-8")))

            krea_default = store.build({"workflow_id": "krea2-moss-dual", "prompt": "style_demo, test"})
            self.assertEqual(krea_default["prompt"]["22"]["inputs"]["shift"], 5.0)

            krea_shifted = store.build({"workflow_id": "krea2-moss-dual", "prompt": "style_demo, test", "shift": 4.2})
            self.assertEqual(krea_shifted["prompt"]["22"]["inputs"]["shift"], 4.2)

            base_shifted = store.build({"workflow_id": "moss-base", "prompt": "style_demo, test", "shift": 6.5, "sampler": "res_multistep"})
            self.assertEqual(base_shifted["prompt"]["2"]["inputs"]["shift"], 6.5)
            self.assertEqual(base_shifted["prompt"]["9"]["inputs"]["sampler_name"], "res_multistep")

            for bad in (-0.5, 105, "nan", True):
                with self.assertRaises(WorkflowError):
                    store.build({"workflow_id": "krea2-moss-dual", "prompt": "x", "shift": bad})

    def test_moss_sft_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            record = store.save(json.loads(MOSS_SFT_FILE.read_text("utf-8")))
            self.assertEqual(record["id"], "moss-sft")
            self.assertEqual(record["name"], "MOSS Image SFT (INT8)")

            default_built = store.build({"workflow_id": "moss-sft", "prompt": "style_demo, dynamic dance"})
            dp = default_built["prompt"]
            self.assertEqual(dp["1"]["inputs"]["unet_name"], "moss_image_sft_int8_convrot.safetensors")
            self.assertEqual(dp["2"]["inputs"]["shift"], 3.5)
            self.assertEqual(dp["9"]["inputs"]["sampler_name"], "er_sde")
            self.assertEqual(dp["9"]["inputs"]["scheduler"], "simple")
            self.assertEqual(dp["9"]["inputs"]["cfg"], 3.5)
            self.assertEqual(dp["9"]["inputs"]["steps"], 28)
            self.assertEqual(dp["8"]["inputs"]["width"], 832)
            self.assertEqual(dp["8"]["inputs"]["height"], 1248)

            custom_built = store.build(
                {
                    "workflow_id": "moss-sft",
                    "prompt": "style_demo, highres test",
                    "shift": 4.0,
                    "sampler": "res_multistep",
                    "scheduler": "beta",
                    "width": 3840,
                    "height": 2160,
                    "steps": 30,
                    "cfg": 3.2,
                }
            )
            cp = custom_built["prompt"]
            self.assertEqual(cp["2"]["inputs"]["shift"], 4.0)
            self.assertEqual(cp["9"]["inputs"]["sampler_name"], "res_multistep")
            self.assertEqual(cp["9"]["inputs"]["scheduler"], "beta")
            self.assertEqual(cp["8"]["inputs"]["width"], 3840)
            self.assertEqual(cp["8"]["inputs"]["height"], 2160)
            self.assertEqual(cp["9"]["inputs"]["steps"], 30)
            self.assertEqual(cp["9"]["inputs"]["cfg"], 3.2)

    def test_moss_base_defaults_distinct_from_sft(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            base_rec = store.save(json.loads(MOSS_BASE_FILE.read_text("utf-8")))
            sft_rec = store.save(json.loads(MOSS_SFT_FILE.read_text("utf-8")))
            self.assertEqual(base_rec["id"], "moss-base")
            self.assertEqual(base_rec["name"], "MOSS Image Base (BF16)")
            self.assertEqual(sft_rec["name"], "MOSS Image SFT (INT8)")

            built_base = store.build({"workflow_id": "moss-base", "prompt": "style_demo, courtyard"})
            bp = built_base["prompt"]
            self.assertEqual(bp["1"]["inputs"]["unet_name"], "moss_image_base_bf16.safetensors")
            self.assertEqual(bp["2"]["inputs"]["shift"], 5.0)
            self.assertEqual(bp["2"]["inputs"]["sampling"], "flow")
            self.assertEqual(bp["8"]["inputs"]["width"], 896)
            self.assertEqual(bp["8"]["inputs"]["height"], 1344)
            self.assertEqual(bp["9"]["inputs"]["steps"], 36)
            self.assertEqual(bp["9"]["inputs"]["cfg"], 3.2)
            self.assertEqual(bp["9"]["inputs"]["sampler_name"], "euler")
            self.assertEqual(bp["9"]["inputs"]["scheduler"], "sgm_uniform")

            built_sft = store.build({"workflow_id": "moss-sft", "prompt": "style_demo, comic"})
            sp = built_sft["prompt"]
            self.assertNotEqual(bp["1"]["inputs"]["unet_name"], sp["1"]["inputs"]["unet_name"])
            self.assertNotEqual(bp["2"]["inputs"]["shift"], sp["2"]["inputs"]["shift"])
            self.assertNotEqual(bp["9"]["inputs"]["sampler_name"], sp["9"]["inputs"]["sampler_name"])
            self.assertNotEqual(bp["9"]["inputs"]["scheduler"], sp["9"]["inputs"]["scheduler"])

    def test_builtin_moss_inpaint_and_img2img(self):
        catalog = [
            {"id": "moss-base-id", "name": "moss_image_base_bf16.safetensors", "filename": "moss_image_base_bf16.safetensors", "kind": "diffusion_models"},
            {"id": "moss-sft-id", "name": "moss_image_sft_int8_convrot.safetensors", "filename": "moss_image_sft_int8_convrot.safetensors", "kind": "diffusion_models"},
            {"id": "clip-id", "name": "qwen3.5_9b_int8_convrot.safetensors", "filename": "qwen3.5_9b_int8_convrot.safetensors", "kind": "text_encoders"},
            {"id": "vae-id", "name": "flux2-vae.safetensors", "filename": "flux2-vae.safetensors", "kind": "vae"},
            {"id": "lora-id", "name": "demo_moss_style.safetensors", "filename": "demo_moss_style.safetensors", "kind": "loras"},
            {"id": "sdxl-id", "name": "wai_illustrious_sdxl_v17.safetensors", "filename": "wai_illustrious_sdxl_v17.safetensors", "kind": "checkpoints"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))

            with self.assertRaises(WorkflowError):
                store.build({"mode": "inpaint", "model": "moss-base-id", "prompt": "fix hands"}, models=catalog)

            inpaint_base = store.build(
                {
                    "mode": "inpaint",
                    "model": "moss-base-id",
                    "vae": "vae-id",
                    "loras": [{"id": "lora-id", "name": "demo_moss_style.safetensors", "strength": 0.85}],
                    "prompt": "style_demo, clean hands",
                    "negative_prompt": "lowres",
                    "input_image": "src.png",
                    "mask": "msk.png",
                    "denoise": 0.65,
                    "sampler": "er_sde",
                    "scheduler": "simple",
                    "cfg": 3.2,
                    "steps": 28,
                },
                models=catalog,
            )
            p_base = inpaint_base["prompt"]
            classes_base = {n["class_type"] for n in p_base.values()}
            self.assertNotIn("CheckpointLoaderSimple", classes_base)
            self.assertNotIn("VAEEncodeForInpaint", classes_base)
            for req_cls in ("UNETLoader", "ModelSamplingAuraFlow", "LoraLoaderModelOnly", "CLIPLoader", "VAELoader", "LoadImage", "ImageToMask", "GrowMask", "VAEEncode", "SetLatentNoiseMask", "KSampler", "VAEDecode", "ImageCompositeMasked", "SaveImage"):
                self.assertIn(req_cls, classes_base)
            af = next(n for n in p_base.values() if n["class_type"] == "ModelSamplingAuraFlow")
            self.assertEqual(af["inputs"]["shift"], 5.0)
            ks = next(n for n in p_base.values() if n["class_type"] == "KSampler")
            self.assertEqual(ks["inputs"]["denoise"], 0.65)
            self.assertEqual(ks["inputs"]["sampler_name"], "er_sde")
            comp = next(n for n in p_base.values() if n["class_type"] == "ImageCompositeMasked")
            self.assertEqual(comp["inputs"]["resize_source"], True)

            img2img_sft = store.build(
                {"mode": "img2img", "model": "moss-sft-id", "image": "base.png", "prompt": "style_demo, repaint", "denoise": 0.5},
                models=catalog,
            )
            p_i2i = img2img_sft["prompt"]
            self.assertIn("VAEEncode", {n["class_type"] for n in p_i2i.values()})
            self.assertNotIn("SetLatentNoiseMask", {n["class_type"] for n in p_i2i.values()})
            af_sft = next(n for n in p_i2i.values() if n["class_type"] == "ModelSamplingAuraFlow")
            self.assertEqual(af_sft["inputs"]["shift"], 3.5)

            txt2img_sft = store.build(
                {"mode": "txt2img", "model": "moss-sft-id", "prompt": "style_demo", "shift": 4.2, "width": 832, "height": 1248},
                models=catalog,
            )
            p_t2i = txt2img_sft["prompt"]
            self.assertIn("EmptyFlux2LatentImage", {n["class_type"] for n in p_t2i.values()})
            self.assertEqual(next(n for n in p_t2i.values() if n["class_type"] == "ModelSamplingAuraFlow")["inputs"]["shift"], 4.2)

            sdxl_inp = store.build(
                {"mode": "inpaint", "model": "sdxl-id", "image": "base.png", "mask": "m.png", "prompt": "fix"},
                models=catalog,
            )
            classes_sdxl = {n["class_type"] for n in sdxl_inp["prompt"].values()}
            self.assertIn("CheckpointLoaderSimple", classes_sdxl)
            self.assertIn("VAEEncodeForInpaint", classes_sdxl)
            self.assertNotIn("UNETLoader", classes_sdxl)

    def test_imported_moss_workflow_splices_multiple_extra_loras(self):
        catalog = [
            {"id": "horse-id", "name": "wooden_horse_Moss.safetensors", "filename": "wooden_horse_Moss.safetensors", "kind": "loras"},
            {"id": "shibari-id", "name": "shibaribondageV1.1-moss.safetensors", "filename": "shibaribondageV1.1-moss.safetensors", "kind": "loras"},
            {"id": "disabled-id", "name": "straight-arm-moss.safetensors", "filename": "straight-arm-moss.safetensors", "kind": "loras"},
        ]
        for wf_file, wid in ((MOSS_SFT_FILE, "moss-sft"), (MOSS_BASE_FILE, "moss-base")):
            with self.subTest(wid=wid), tempfile.TemporaryDirectory() as tmp:
                store = WorkflowStore(Path(tmp))
                store.save(json.loads(wf_file.read_text("utf-8")))
                built = store.build(
                    {
                        "workflow_id": wid,
                        "prompt": "a wooden submission horse, test",
                        "loras": [
                            {"id": "horse-id", "strength": 0.8},
                            {"id": "shibari-id", "strength": 0.85},
                            {"id": "disabled-id", "enabled": False, "strength": 0.9},
                        ],
                    },
                    models=catalog,
                )
                prompt = built["prompt"]
                self.assertIn("extra_lora_1", prompt)
                self.assertIn("extra_lora_2", prompt)
                self.assertNotIn("extra_lora_3", prompt)
                self.assertEqual(prompt["extra_lora_1"]["class_type"], "LoraLoaderModelOnly")
                self.assertEqual(prompt["extra_lora_1"]["inputs"], {"model": ["2", 0], "lora_name": "wooden_horse_Moss.safetensors", "strength_model": 0.8})
                self.assertEqual(prompt["extra_lora_2"]["inputs"], {"model": ["extra_lora_1", 0], "lora_name": "shibaribondageV1.1-moss.safetensors", "strength_model": 0.85})
                self.assertEqual(prompt["9"]["inputs"]["model"], ["extra_lora_2", 0])
                req_loras = [m.get("comfy_name") or m.get("filename") for m in built["required_models"] if m.get("kind") == "loras"]
                self.assertIn("wooden_horse_Moss.safetensors", req_loras)
                self.assertIn("shibaribondageV1.1-moss.safetensors", req_loras)
                self.assertNotIn("straight-arm-moss.safetensors", req_loras)

    def test_krea2_dual_stage_splices_extra_lora_onto_moss_stage(self):
        catalog = [
            {"id": "fx-id", "name": "moss_manga_style_fx_v1.safetensors", "filename": "moss_manga_style_fx_v1.safetensors", "kind": "loras"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.save(self._load())
            built = store.build(
                {
                    "workflow_id": "krea2-moss-dual",
                    "prompt": "comic action page",
                    "loras": [{"id": "fx-id", "strength": 0.65}],
                },
                models=catalog,
            )
            prompt = built["prompt"]
            self.assertEqual(prompt["extra_lora_1"]["inputs"], {"model": ["22", 0], "lora_name": "moss_manga_style_fx_v1.safetensors", "strength_model": 0.65})
            self.assertEqual(prompt["30"]["inputs"]["model"], ["extra_lora_1", 0])
            self.assertEqual(prompt["11"]["inputs"]["model"], ["1", 0])

    def test_seedvr2_pack_workflows_build_without_private_loras(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            anima_rec = store.save(json.loads(ANIMA_PACK_FILE.read_text("utf-8")))
            illus_rec = store.save(json.loads(ILLUSTRIOUS_PACK_FILE.read_text("utf-8")))
            self.assertEqual(anima_rec["id"], "pack-anima-seedvr2")
            self.assertEqual(illus_rec["id"], "pack-illustrious-seedvr2")

            anima_built = store.build(
                {
                    "workflow_id": "pack-anima-seedvr2",
                    "prompt": "honkai:star rail offcial art, 1girl, starry train",
                    "loras": [{"node_id": "6", "id": "anima_hsr_cg.safetensors", "strength": 0.8}],
                }
            )
            self.assertEqual(anima_built["prompt"]["6"]["inputs"]["strength_model"], 0.8)
            self.assertEqual(anima_built["prompt"]["6"]["inputs"]["model"], ["2", 0])

            illus_built = store.build({"workflow_id": "pack-illustrious-seedvr2", "prompt": "1girl, portrait", "seed": 77})
            self.assertEqual(illus_built["prompt"]["7"]["inputs"]["model"], ["2", 0])
            self.assertEqual(illus_built["prompt"]["7"]["inputs"]["seed"], 77)
            self.assertEqual(illus_built["prompt"]["12"]["inputs"]["seed"], 77)

    def test_moss_layout_json_prompt_preserved_verbatim(self):
        layout_json = json.dumps(
            {
                "summary": {"meta": ["poster"], "canvas": "single", "caption": "red autumn poster"},
                "items": [
                    {"label": "text", "caption": "Vermilion calligraphy title.", "text": "山海\n绘卷", "bbox": [-520, -320, -180, -80]},
                    {"label": "character", "caption": "Main heroine standing in autumn courtyard.", "bbox": [-480, -240, 580, 360]},
                ],
                "bbox_coord_space": "abs_centralized",
                "bbox_format": "yxyx",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.save(json.loads(MOSS_SFT_FILE.read_text("utf-8")))
            built = store.build({"workflow_id": "moss-sft", "prompt": layout_json})
            self.assertEqual(built["prompt"]["6"]["inputs"]["text"], layout_json)


if __name__ == "__main__":
    unittest.main()
