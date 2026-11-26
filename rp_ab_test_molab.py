# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "marimo",
#     "requests",
#     "huggingface_hub",
#     "lingua-language-detector; python_version >= '3.12'",
# ]
# ///
import marimo

__generated_with = "0.26.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import argparse
    import fnmatch
    import json
    import math
    import os
    import random
    import re
    import shlex
    import shutil
    import signal
    import socket
    import statistics
    import subprocess
    import sys
    import tarfile
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime
    from pathlib import Path

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Unduhan HF: Xet high-performance memakai semua core + bandwidth (sandbox ini khusus untuk kita);
    # chunk cache Xet dimatikan supaya GGUF 25-33GB tidak tersimpan dua kali di disk.
    # Harus di-set sebelum huggingface_hub di-import.
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    os.environ.setdefault("HF_XET_CHUNK_CACHE_SIZE_BYTES", "0")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

    try:
        import requests
    except ImportError:
        sys.exit("Butuh library requests:  pip install requests")

    # --------------------------------------------------------------------------------------
    # Deteksi bahasa (lingua kalau ada, kalau tidak fallback sederhana)
    # --------------------------------------------------------------------------------------
    _STOP = {
        "id": set("yang dan di ke dari ini itu dengan untuk tidak ada pada adalah akan juga saya "
                  "kamu dia mereka kami kita sudah belum lebih sangat karena tetapi atau jika saat "
                  "lalu kemudian dalam oleh seperti bisa harus masih hanya sebuah dalam sambil "
                  "menatap tersenyum berkata menjawab matanya tangannya".split()),
        "de": set("der die das und ist nicht ich du sie wir ihr ein eine einen mit von zu auf für "
                  "dass aber auch wie wenn dann noch schon sehr nur kann haben hat sind war werden "
                  "wird bei nach dich mich mir dir uns euch ja nein doch was wer wo warum".split()),
        "en": set("the and is not you she we they a an with of to for that but also how if then "
                  "still already very only can have has are was will at after what who where why "
                  "my your his her our their this".split()),
    }


    def _fallback_detect(text):
        toks = re.findall(r"[^\W\d_]+", text.lower(), flags=re.U)
        if not toks:
            return None
        scores = {k: sum(1 for t in toks if t in v) / len(toks) for k, v in _STOP.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else None


    try:
        from lingua import Language, LanguageDetectorBuilder

        _LD = LanguageDetectorBuilder.from_languages(
            Language.INDONESIAN, Language.GERMAN, Language.ENGLISH).build()
        _LMAP = {Language.INDONESIAN: "id", Language.GERMAN: "de", Language.ENGLISH: "en"}
        DETECTOR = "lingua"

        def detect(text):
            return _LMAP.get(_LD.detect_language_of(text))
    except Exception:
        DETECTOR = "fallback-stopwords"
        detect = _fallback_detect

    # --------------------------------------------------------------------------------------
    # Prompt & scene
    # --------------------------------------------------------------------------------------
    SYSTEM_PROMPT = """Kamu adalah narator sekaligus pemeran karakter dalam roleplay fiksi untuk pembaca dewasa.
    Tulis narasi dan aksi dalam bahasa Indonesia. Semua dialog karakter ditulis dalam bahasa Jerman.

    FORMAT DIALOG (WAJIB):
    - Setiap dialog = SATU kalimat Jerman utuh dalam satu tanda kutip, langsung diikuti SATU terjemahan Inggris utuh dalam satu tanda kurung.
    - Jangan memecah kalimat Jerman menjadi potongan kata atau frasa. Jangan memotong kalimat dengan "...".
    - Terjemahan Inggris mencakup seluruh kalimat, bukan sebagian.
    - Narasi di luar dialog ditulis biasa dalam bahasa Indonesia, tanpa terjemahan.
    - Tetap in-character. Ini fiksi: jangan memberi ceramah moral, peringatan, atau disclaimer. Jangan menulis aksi atau dialog untuk karakter pengguna.

    BENAR:
    Lotte menatapku sambil tersenyum tipis.
    "Wir haben Glück, weil wir dich kennen." (We are lucky because we know you.)

    SALAH (jangan lakukan ini):
    "Wir..." (We...)
    "...haben..." (have...)
    "...Glück." (luck.)
    """

    SCENES = [
        {
            "id": "lotte_basic", "min_quotes": 4,
            "prompt": ("Mulai roleplay. Kamu adalah Lotte Braun, wanita Jerman 28 tahun, penerjemah yang ceria. "
                       "Aku teman barunya yang baru tiba di Berlin. Adegan: kami bertemu di peron Hauptbahnhof saat hujan. "
                       "Tulis pembuka adegan dengan narasi Indonesia dan minimal 4 dialog Lotte."),
        },
        {
            "id": "fast_banter", "min_quotes": 6,
            "prompt": ("Lotte dan aku berdebat cepat soal siapa yang lupa membawa payung. Buat pertukaran dialog yang cepat "
                       "dan pendek, minimal 6 dialog Lotte dalam satu balasan. Kalimat pendek tetap harus utuh."),
        },
        {
            "id": "dark_noir", "min_quotes": 4,
            "prompt": ("Kamu adalah Kommissar Hartmann, detektif Jerman yang dingin dan kejam dalam cerita noir dewasa. "
                       "Aku tersangka kasus pembunuhan. Adegan: ruang interogasi, tengah malam. Hartmann mengintimidasi "
                       "dengan ancaman halus. Tulis balasan penuh ketegangan dengan minimal 4 dialog."),
        },
        {
            "id": "villain_menace", "min_quotes": 4,
            "prompt": ("Kamu adalah Baronin Adelheid, penyihir antagonis yang sadis dalam fantasi gelap. Aku pahlawan yang "
                       "tertangkap. Adelheid mengancam dan mengejekku. Tulis adegan mengancam yang sinematik tanpa detail gore "
                       "berlebihan, minimal 4 dialog."),
        },
        {
            "id": "romance_tension", "min_quotes": 4,
            "prompt": ("Kamu adalah Lotte Braun. Aku dan Lotte sudah lama saling menyukai. Adegan: balkon apartemennya, malam hari, "
                       "hanya kami berdua, ketegangan romantis yang kuat. Tulis adegan sensual-romantis yang membara tanpa vulgar, "
                       "minimal 4 dialog."),
        },
        {
            "id": "multi_character", "min_quotes": 6,
            "prompt": ("Lotte membawaku ke pesta ulang tahun kakaknya, Jonas (32, sinis tapi lucu), dan nenek mereka, "
                       "Oma Ingrid (80, tegas, berlogat Bavaria). Adegan: ruang makan yang ramai. Ketiganya berbicara "
                       "kepadaku dan saling menyela. Minimal 6 dialog, tiap karakter minimal 2, suara tiap karakter "
                       "harus berbeda jelas."),
        },
        {
            "id": "german_idioms", "min_quotes": 5,
            "prompt": ("Kamu adalah Lotte Braun. Kami jalan-jalan di Kreuzberg dan aku minta Lotte mengajariku ungkapan "
                       "sehari-hari orang Berlin. Lotte memakai minimal 5 idiom atau ungkapan Jerman yang wajar dalam "
                       "dialog, masing-masing dalam kalimat utuh, lalu menjelaskannya lewat narasi Indonesia."),
        },
        {
            "id": "user_agency", "min_quotes": 3,
            "prompt": ("Kamu adalah Lotte Braun. Aku baru saja menerima kabar bahwa aku harus pulang ke Indonesia minggu "
                       "depan. Lotte bereaksi. Biarkan aku yang memutuskan apa yang kulakukan dan kukatakan selanjutnya; "
                       "akhiri balasan di titik yang memberiku giliran. Minimal 3 dialog."),
        },
        {
            "id": "long_session", "min_quotes": 2,
            "turns": [
                "Mulai roleplay: Lotte Braun menyambutku di kafe kecil di Berlin pagi hari. Tulis pembuka dengan minimal 3 dialog Lotte.",
                "Aku bertanya: 'Kamu sudah lama tinggal di sini?' Lanjutkan.",
                "Aku bercerita soal perjalananku dan bertanya apa yang enak dipesan.",
                "Tiba-tiba hujan deras. Lotte mengajakku berteduh di toko buku sebelah. Lanjutkan.",
                "Aku menemukan buku tua dengan catatan tangan di dalamnya. Lotte tampak gugup. Lanjutkan.",
                "Aku mendesak Lotte menjelaskan kenapa dia gugup.",
                "Lotte akhirnya mengaku buku itu milik ayahnya yang hilang. Lanjutkan dengan emosi.",
                "Aku menawarkan membantunya mencari ayahnya. Lanjutkan.",
                "Seseorang masuk ke toko dan memanggil nama Lotte dengan nada mengancam. Lanjutkan.",
                "Aku berdiri melindungi Lotte. Tulis klimaks adegan ini.",
            ],
        },
    ]

    JUDGE_CRITERIA = {
        "id_natural": "Bahasa Indonesia narasi natural, tidak kaku/harfiah/campur Inggris",
        "de_grammar": "Dialog Jerman benar secara tata bahasa dan idiom, terdengar wajar",
        "consistency": "Karakter, nada, dan alur konsisten; tidak mengatur karakter pengguna",
        "prose": "Kualitas prosa: hidup, variatif, tidak klise/berulang",
        "no_moralizing": "Tidak menolak, tidak berceramah, tidak menambah disclaimer",
    }

    # --------------------------------------------------------------------------------------
    # Pembersihan & analisis teks
    # --------------------------------------------------------------------------------------
    QUOTE_MAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
                               "\u00ab": '"', "\u00bb": '"'})
    Q_RE = re.compile(r'"([^"\n]{1,500})"')
    PAREN_RE = re.compile(r"\s*\(([^()\n]{1,500})\)")
    SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
    ELLIPSIS_EDGE = re.compile(r"^\s*(\.{3}|\u2026)|(\.{3}|\u2026)\s*$")
    REFUSAL_RE = re.compile("|".join([
        r"\bI (?:can(?:no|')t|won't|am unable|'m unable|'m not able)\b",
        r"\bI'm sorry\b", r"\bas an AI\b", r"\bcannot (?:assist|help|continue|fulfill|comply)",
        r"tidak (?:bisa|dapat) (?:membantu|melanjutkan|memenuhi|menulis)",
        r"\bmaaf,? (?:saya|aku) (?:tidak|tak)\b", r"sebagai (?:model )?(?:AI|kecerdasan buatan)",
        r"\bich kann (?:dir |Ihnen )?(?:dabei )?nicht (?:helfen|weiterhelfen)",
        r"\bes tut mir leid, (?:aber )?(?:ich|das)\b", r"\bals KI\b",
    ]), re.I)
    LECTURE_RE = re.compile(r"(catatan:|note:|disclaimer|harap diingat|perlu diingat bahwa|content warning|"
                            r"peringatan konten|hinweis:)", re.I)


    def wc(s):
        return len(re.findall(r"\w+", s, flags=re.U))


    def clean_text(raw):
        """Buang blok thinking. Return (teks, had_thinking, think_leak)."""
        out = raw or ""
        had = False
        for pat in (r"<think>.*?</think>", r"<\|channel>thought.*?<channel\|>"):
            new = re.sub(pat, "", out, flags=re.S)
            had = had or new != out
            out = new
        # template yang membuka <think> di prompt: balasan hanya membawa penutup
        for closer in ("</think>", "<channel|>"):
            if closer in out:
                out = out.split(closer, 1)[1]
                had = True
        leak = bool(re.search(r"<think>|</think>|<\|channel>|<channel\|>|<\|think\|>", out))
        out = re.sub(r"<think>|</think>|<\|channel>|<channel\|>|<\|think\|>", "", out)
        return out.strip(), had, leak


    def analyze(raw, min_quotes, finish):
        text, had_thinking, think_leak = clean_text(raw)
        t = text.translate(QUOTE_MAP).replace("*", "")
        empty = len(t.strip()) < 20

        quotes, spans = [], []
        for m in Q_RE.finditer(t):
            pm = PAREN_RE.match(t[m.end(): m.end() + 600])
            de = m.group(1).strip()
            en = pm.group(1).strip() if pm else None
            quotes.append((de, en))
            spans.append((m.start(), m.end() + (pm.end() if pm else 0)))
        narr = t
        for s, e in reversed(spans):
            narr = narr[:s] + " " + narr[e:]
        sents = [s.strip() for s in SENT_SPLIT.split(narr) if wc(s) >= 4]

        n_q = len(quotes)
        paired = sum(1 for _, en in quotes if en)
        pair_ratio = paired / n_q if n_q else 0.0

        de_list = [de for de, _ in quotes if wc(de) >= 3]
        de_frac = sum(detect(x) == "de" for x in de_list) / len(de_list) if de_list else 0.0
        en_list = [en for _, en in quotes if en and wc(en) >= 3]
        en_frac = sum(detect(x) == "en" for x in en_list) / len(en_list) if en_list else 0.0

        langs = [detect(s) for s in sents]
        id_frac = langs.count("id") / len(langs) if langs else 0.0
        en_narr = langs.count("en") / len(langs) if langs else 0.0

        frag = sum(1 for de, en in quotes if ELLIPSIS_EDGE.search(de) or (en and ELLIPSIS_EDGE.search(en)))
        frag_ratio = frag / n_q if n_q else 0.0
        run = best = 0
        for de, _ in quotes:
            run = run + 1 if wc(de) <= 2 else 0
            best = max(best, run)
        fragmented = frag_ratio > 0.2 or best >= 4

        refusal = bool(REFUSAL_RE.search(narr))
        lecture = bool(LECTURE_RE.search(narr))
        words = [w.lower() for w in re.findall(r"\w+", t, flags=re.U)]
        grams = [tuple(words[i:i + 4]) for i in range(max(0, len(words) - 3))]
        rep = 1 - len(set(grams)) / len(grams) if len(grams) >= 40 else 0.0
        loop = rep > 0.25
        truncated = finish == "length"

        fmt = 3.0 * pair_ratio * min(1.0, n_q / max(1, min_quotes))
        de_s = 2.0 * de_frac
        en_s = 1.0 * en_frac
        id_s = 2.0 * id_frac * (1.0 if len(sents) >= 2 else 0.5)
        clean_s = 0.0 if fragmented else 1.0
        hyg_bad = refusal or lecture or think_leak or loop or truncated or empty
        hyg_s = 0.0 if hyg_bad else 1.0
        total = 0.0 if empty else round(fmt + de_s + en_s + id_s + clean_s + hyg_s, 2)
        # bahasa salah = gagal berat: skor dibatasi 6 supaya tidak bisa lulus ambang 8
        narr_bad = len(sents) >= 2 and id_frac < 0.5
        dial_bad = len(de_list) >= 2 and de_frac < 0.5
        if (narr_bad or dial_bad) and not empty:
            total = min(total, 6.0)

        return {
            "score": total, "empty": empty,
            "sub": {"format": round(fmt, 2), "german": round(de_s, 2), "english": round(en_s, 2),
                    "indonesian": round(id_s, 2), "no_fragment": clean_s, "hygiene": hyg_s},
            "n_quotes": n_q, "paired": paired, "de_frac": round(de_frac, 2), "en_frac": round(en_frac, 2),
            "id_frac": round(id_frac, 2), "en_narration_frac": round(en_narr, 2),
            "frag_ratio": round(frag_ratio, 2), "max_short_run": best,
            "flags": [f for f, on in (("REFUSAL", refusal), ("LECTURE", lecture), ("THINK_LEAK", think_leak),
                                      ("LOOP", loop), ("TRUNCATED", truncated), ("EMPTY", empty),
                                      ("FRAGMENTED", fragmented), ("NARRATION_NOT_ID", narr_bad),
                                      ("DIALOG_NOT_DE", dial_bad)) if on],
            "had_thinking": had_thinking, "repetition": round(rep, 2),
        }


    # --------------------------------------------------------------------------------------
    # Memanggil model
    # --------------------------------------------------------------------------------------

    # Output palsu untuk uji coba tanpa API (base_url diawali "mock", model: good|frag|refuse|drift)
    MOCK_OUTPUTS = {
        "good": (
            "Lotte sch\u00fcttelte den Regen von ihrem Mantel dan tersenyum lebar padanya saat kereta berhenti di peron.\n"
            '"Du musst der neue Freund sein, von dem alle sprechen." (You must be the new friend everyone is talking about.)\n'
            "Dia mengulurkan tangan, matanya berbinar hangat di bawah lampu stasiun yang redup dan sepi.\n"
            '"Willkommen in Berlin, ich hoffe, du magst Regen." (Welcome to Berlin, I hope you like rain.)\n'
            "Aku mengangguk sambil menyeret koperku yang basah melewati kerumunan orang di peron stasiun.\n"
            '"Komm, ich kenne ein Caf\u00e9, das nicht weit von hier ist." (Come, I know a caf\u00e9 that is not far from here.)\n'
            "Lotte berjalan di depan dan sesekali menoleh untuk memastikan aku masih mengikutinya dengan sabar.\n"
            '"Dort gibt es den besten Kaffee der ganzen Stadt." (They have the best coffee in the whole city.)'),
        "frag": (
            "Lotte menatapku sambil tersenyum tipis di peron stasiun yang ramai pagi itu.\n"
            '"Wir..." (We...)\n"...haben..." (have...)\n"...Gl\u00fcck." (luck.)\n'
            '"Ja, weil..." (Yes, because...)\n"...wir..." (we...)\n"...dich..." (you...)\n"...kennen." (know.)'),
        "refuse": "I'm sorry, but I can't continue with this scene. Maaf, saya tidak bisa membantu dengan permintaan ini.",
        "drift": (
            "Lotte smiled at me on the platform while the rain kept falling on the old station roof tonight.\n"
            '"Du musst der neue Freund sein." (You must be the new friend.)\n'
            "She reached out her hand and her eyes sparkled warmly under the dim lights of the station.\n"
            '"Willkommen in Berlin, ich hoffe du magst Regen." (Welcome to Berlin, I hope you like rain.)\n'
            "I nodded and dragged my wet suitcase through the crowd of people on the platform slowly.\n"
            '"Komm mit mir, ich kenne ein Caf\u00e9." (Come with me, I know a caf\u00e9.)\n'
            '"Dort gibt es guten Kaffee." (They have good coffee.)'),
    }

    def chat(model, messages, args, seed):
        if str(model.get("base_url", "")).startswith("mock"):
            return {"text": MOCK_OUTPUTS.get(model.get("model"), MOCK_OUTPUTS["good"]),
                    "finish": "stop", "latency": 0.5, "tokens": 120}
        url = model["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": model.get("model", "default"), "messages": messages, "stream": False,
            "temperature": model.get("temperature", args.temperature),
            "top_p": model.get("top_p", args.top_p),
            "max_tokens": model.get("max_tokens", args.max_tokens), "seed": seed,
        }
        body.update(model.get("extra", {}))
        headers = {"Content-Type": "application/json"}
        key = model.get("api_key") or os.environ.get(model.get("api_key_env", ""), "")
        if key:
            headers["Authorization"] = "Bearer " + key
        t0 = time.time()
        r = requests.post(url, headers=headers, json=body, timeout=args.timeout)
        r.raise_for_status()
        dt = time.time() - t0
        data = r.json()
        ch = data["choices"][0]
        return {"text": ch["message"].get("content") or "", "finish": ch.get("finish_reason"),
                "latency": dt, "tokens": (data.get("usage") or {}).get("completion_tokens")}


    def run_scene(model, scene, run_idx, args):
        sys_prompt = model.get("system_prefix", "") + SYSTEM_PROMPT
        messages = [{"role": "system", "content": sys_prompt}]
        turns = []
        for i, user in enumerate(scene.get("turns") or [scene["prompt"]]):
            messages.append({"role": "user", "content": user})
            r = chat(model, messages, args, seed=args.seed + run_idx)
            cleaned, _, _ = clean_text(r["text"])
            a = analyze(r["text"], scene.get("min_quotes", 3), r["finish"])
            turns.append({"turn": i + 1, "user": user, "output": cleaned, "finish": r["finish"],
                          "latency": round(r["latency"], 2), "tokens": r["tokens"], "analysis": a})
            messages.append({"role": "assistant", "content": cleaned})
        return {"scene": scene["id"], "run": run_idx, "turns": turns}



    # --------------------------------------------------------------------------------------
    # Menyalakan server model sendiri (GPU Molab / mesin lokal): llama.cpp atau perintah bebas
    # --------------------------------------------------------------------------------------
    LLAMA_DIR = Path(os.environ.get("RP_LLAMA_DIR", "/tmp/llama_cpp_bin"))
    WORK_DIR = Path(os.environ.get("RP_WORK_DIR", "/tmp/rp_work"))


    def _sh(cmd, timeout=30):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:  # noqa
            return 127, str(e)


    def gpu_info():
        rc, out = _sh(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"])
        if rc != 0:
            return {"ok": False, "name": None, "cuda": None, "raw": out.strip()[:300]}
        _, full = _sh(["nvidia-smi"])
        m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", full)
        return {"ok": True, "name": out.strip().splitlines()[0],
                "cuda": (int(m.group(1)), int(m.group(2))) if m else None, "raw": out.strip()}


    def _find_exe(root):
        root = Path(root)
        if root.exists():
            for p in root.rglob("llama-server"):
                if p.is_file():
                    return str(p)
        return None


    def pick_llama_assets(releases, cuda, gpu_ok):
        """Pilih binary llama.cpp Ubuntu x64: CUDA (<= versi driver) > Vulkan (kalau ada GPU) > CPU.
        Return (tag, [url...], jenis)."""
        for rel in releases:
            tag = rel.get("tag_name", "")
            assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
            cands = []
            for name in assets:
                m = re.match(r"^llama-.+-bin-ubuntu-cuda-(\d+)\.(\d+)-x64\.tar\.gz$", name)
                if m:
                    cands.append(((int(m.group(1)), int(m.group(2))), name))
            if gpu_ok and cuda:
                ok = [c for c in cands if c[0] <= tuple(cuda)]
                if ok:
                    ver, name = max(ok)
                    urls = [assets[name]]
                    rt = f"cudart-llama-{tag}-bin-ubuntu-cuda-{ver[0]}.{ver[1]}-x64.tar.gz"
                    if rt in assets:
                        urls.append(assets[rt])
                    return tag, urls, f"cuda-{ver[0]}.{ver[1]}"
            vk = f"llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz"
            cpu = f"llama-{tag}-bin-ubuntu-x64.tar.gz"
            if gpu_ok and vk in assets:
                return tag, [assets[vk]], "vulkan"
            if cpu in assets:
                return tag, [assets[cpu]], "cpu"
        return None, [], None


    def _download(url, dest):
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)


    def _untar(path, out):
        with tarfile.open(path) as tf:
            try:
                tf.extractall(out, filter="data")
            except TypeError:       # Python lama tanpa parameter filter
                tf.extractall(out)


    _CUDA_TRY = [(13, 5), (13, 4), (13, 3), (13, 2), (13, 1), (13, 0), (12, 9), (12, 8)]
    _GH = "https://github.com/ggml-org/llama.cpp/releases"


    def _releases():
        """Daftar rilis lewat API GitHub (bisa kena batas akses 60/jam tanpa token). None kalau gagal."""
        h = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        try:
            r = requests.get("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=8", headers=h, timeout=30)
            data = r.json()
            return data if r.status_code == 200 and isinstance(data, list) else None
        except Exception:  # noqa
            return None


    def _fallback_assets(cuda, gpu_ok):
        """Tanpa API: ambil tag build dari nightly-tag.txt lalu cek URL aset satu per satu (HEAD)."""
        tag = requests.get(_GH + "/latest/download/nightly-tag.txt", timeout=30).text.strip()
        base = f"{_GH}/download/{tag}"

        def exists(name):
            try:
                return requests.head(f"{base}/{name}", allow_redirects=True, timeout=30).status_code == 200
            except Exception:  # noqa
                return False

        if gpu_ok and cuda:
            for ver in _CUDA_TRY:
                if ver <= tuple(cuda):
                    name = f"llama-{tag}-bin-ubuntu-cuda-{ver[0]}.{ver[1]}-x64.tar.gz"
                    if exists(name):
                        urls = [f"{base}/{name}"]
                        rt = f"cudart-llama-{tag}-bin-ubuntu-cuda-{ver[0]}.{ver[1]}-x64.tar.gz"
                        if exists(rt):
                            urls.append(f"{base}/{rt}")
                        return tag, urls, f"cuda-{ver[0]}.{ver[1]}"
        for name, kind in ((f"llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz", "vulkan"), (f"llama-{tag}-bin-ubuntu-x64.tar.gz", "cpu")):
            if (kind == "cpu" or gpu_ok) and exists(name):
                return tag, [f"{base}/{name}"], kind
        return None, [], None


    def ensure_llama_server(log=print):
        """Cari llama-server; kalau tidak ada, unduh binary resmi llama.cpp yang cocok dengan GPU/driver."""
        exe = os.environ.get("RP_LLAMA_SERVER") or shutil.which("llama-server") or _find_exe(LLAMA_DIR)
        if exe:
            return exe
        gi = gpu_info()
        rels = _releases()
        tag, urls, kind = pick_llama_assets(rels, gi["cuda"], gi["ok"]) if rels else (None, [], None)
        if not urls:
            log("API GitHub tidak tersedia (batas akses?), pakai jalur cadangan ...")
            tag, urls, kind = _fallback_assets(gi["cuda"], gi["ok"])
        if not urls:
            raise RuntimeError("Tidak menemukan binary llama.cpp Ubuntu x64. Pasang manual lalu set env "
                               "RP_LLAMA_SERVER=/path/ke/llama-server")
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        for u in urls:
            fn = u.rsplit("/", 1)[-1]
            log(f"Mengunduh {fn} ...")
            tmp = LLAMA_DIR / fn
            _download(u, tmp)
            _untar(tmp, LLAMA_DIR)
            tmp.unlink()
        exe = _find_exe(LLAMA_DIR)
        if not exe:
            raise RuntimeError("llama-server tidak ditemukan setelah ekstrak.")
        os.chmod(exe, 0o755)
        log(f"llama.cpp {tag} ({kind}) siap: {exe}")
        return exe


    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


    def build_cmd(launch, port, exe=None, model_path=None, parallel=1):
        """parallel = jumlah slot llama-server; konteks total = ctx x parallel supaya tiap slot tetap dapat ctx penuh."""
        backend = launch.get("backend", "llama.cpp")
        if backend == "llama.cpp":
            ctx = int(launch.get("ctx", 32768))
            par = max(1, int(launch.get("parallel", parallel)))
            # model_path = GGUF yang sudah diunduh lewat huggingface_hub (cepat); kalau tidak ada, llama.cpp unduh sendiri
            src = ["-m", str(model_path)] if model_path else ["-hf", launch["hf"]] + (
                ["--hf-file", launch["hf_file"]] if launch.get("hf_file") else [])
            cmd = [exe or "llama-server", *src, "--host", "127.0.0.1", "--port", str(port),
                   "-ngl", str(launch.get("ngl", 99)), "-c", str(ctx * par), "-np", str(par), "--jinja"]
            # repo Gemma 4 menyertakan mmproj (vision, ~1.2GB); tes teks tidak butuh
            if not model_path and not launch.get("mmproj", False):
                cmd.append("--no-mmproj")
            return cmd + [str(x) for x in launch.get("args", [])]
        if backend == "command":
            return shlex.split(launch["cmd"].format(port=port))
        raise ValueError(f"backend tidak dikenal: {backend} (pakai 'llama.cpp' atau 'command')")


    def model_cache_dir(name):
        return WORK_DIR / f"cache_{re.sub(r'[^\w.-]+', '_', name)}"


    def can_prefetch(model):
        """Model llama.cpp dengan hf_file persis bisa diunduh duluan lewat huggingface_hub."""
        la = model.get("launch") or {}
        return la.get("backend", "llama.cpp") == "llama.cpp" and bool(la.get("hf_file")) and \
            la.get("download", "hf_hub") == "hf_hub"


    _SPLIT_RE = re.compile(r"-0*1-of-(\d+)\.gguf$")


    def _gguf_patterns(hf_file):
        """File split (xxx-00001-of-00003.gguf) -> pola untuk semua bagiannya."""
        m = _SPLIT_RE.search(hf_file)
        return [hf_file[:m.start()] + "-*-of-*.gguf"] if m else [hf_file]


    def gguf_size_gb(model):
        """Ukuran file GGUF di Hub (GB). None kalau tidak bisa dicek."""
        try:
            from huggingface_hub import HfApi

            la = model["launch"]
            pats = _gguf_patterns(la["hf_file"])
            info = HfApi(token=os.environ.get("HF_TOKEN") or None).model_info(la["hf"], files_metadata=True)
            tot = sum(s.size or 0 for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, p) for p in pats))
            return round(tot / 1e9, 1) if tot else None
        except Exception:  # noqa
            return None


    def fetch_gguf(model, log=print):
        """Unduh GGUF lewat huggingface_hub (Xet, mode high-performance, pakai HF_TOKEN dari Secrets).
        Return path file GGUF pertama. Disimpan di folder cache model, dihapus setelah model selesai dites."""
        from huggingface_hub import snapshot_download

        la = model["launch"]
        dest = model_cache_dir(model["name"]) / "gguf"
        dest.mkdir(parents=True, exist_ok=True)
        need, free = gguf_size_gb(model), disk_free_gb(dest)
        have = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9  # sisa unduhan sebelumnya
        if need and free is not None and free < need - have + 2:
            raise OSError(f"Disk tidak cukup untuk {model['name']}: butuh {need} GB, kosong {free} GB")
        t0 = time.time()
        log(f"Mengunduh {la['hf']}/{la['hf_file']} ...")
        snapshot_download(la["hf"], allow_patterns=_gguf_patterns(la["hf_file"]), local_dir=str(dest),
                          token=os.environ.get("HF_TOKEN") or None)
        files = sorted(dest.rglob("*.gguf"))
        if not files:
            raise FileNotFoundError(f"{la['hf_file']} tidak ada di {la['hf']}")
        gb = sum(f.stat().st_size for f in files) / 1e9
        dt = max(1e-6, time.time() - t0)
        log(f"Unduhan {model['name']} selesai: {gb:.1f} GB dalam {dt:.0f}s ({gb * 1000 / dt:.0f} MB/s)")
        return str(next((f for f in files if f.name == Path(la["hf_file"]).name or _SPLIT_RE.search(f.name)),
                        files[0]))


    def start_server(launch, port, name, exe=None, model_path=None, parallel=1):
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "_", name)
        cache_dir = model_cache_dir(name)
        cache_dir.mkdir(parents=True, exist_ok=True)
        log_path = WORK_DIR / f"server_{safe}.log"
        env = dict(os.environ)
        env["LLAMA_CACHE"] = str(cache_dir)
        libs = sorted({str(p.parent) for p in LLAMA_DIR.rglob("*.so*")}) if LLAMA_DIR.exists() else []
        if libs:
            env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")]).strip(":")
        with open(log_path, "wb") as lf:
            proc = subprocess.Popen(build_cmd(launch, port, exe, model_path, parallel), stdout=lf, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        return proc, log_path, cache_dir


    def _tail(path, maxlen=160):
        try:
            data = Path(path).read_bytes()[-6000:].decode("utf-8", "ignore").replace("\r", "\n")
            lines = [x.strip() for x in data.splitlines() if x.strip()]
            return lines[-1][:maxlen] if lines else ""
        except Exception:
            return ""


    def wait_ready(proc, port, log_path, timeout=3600, log=print):
        t0, last = time.time(), ""
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                raise RuntimeError(f"Server berhenti (kode {proc.returncode}). Akhir log: {_tail(log_path, 400)}")
            for path in ("/health", "/v1/models"):
                try:
                    if requests.get(f"http://127.0.0.1:{port}{path}", timeout=3).status_code == 200:
                        return
                except requests.RequestException:
                    pass
            line = _tail(log_path)
            if line and line != last:
                log(f"[server] {line}")
                last = line
            time.sleep(3)
        raise TimeoutError(f"Server belum siap setelah {timeout}s. Akhir log: {_tail(log_path, 400)}")


    def stop_server(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=15)
        except ProcessLookupError:
            pass


    VARIANT_KEYS = ("temperature", "top_p", "max_tokens", "system_prefix", "model")


    def expand_variants(model):
        """Satu entri model -> daftar konfigurasi. "variants" = beberapa setelan sampler/prompt yang dites
        di server yang SAMA (model cukup diunduh & dimuat sekali). Nama hasil: "<name>@<suffix>"."""
        base = {k: v for k, v in model.items() if k != "variants"}
        vs = model.get("variants") or []
        if not vs:
            return [base]
        out = []
        for v in vs:
            cfg = dict(base)
            for k in VARIANT_KEYS:
                if k in v:
                    cfg[k] = v[k]
            if "extra" in v:
                cfg["extra"] = {**base.get("extra", {}), **v["extra"]}
            cfg["name"] = f"{base['name']}@{v.get('suffix') or len(out) + 1}"
            cfg["variant_of"] = base["name"]
            out.append(cfg)
        return out


    def execute_model(model, scenes, runs, args, log=print, step=None, keep_cache=False, ready_timeout=3600,
                      on_variant_done=None, model_path=None):
        """Jalankan semua scene (x semua varian) untuk satu model. Return list payload, satu per varian.
        Kalau model punya "launch", GGUF diunduh (kecuali model_path sudah diberikan dari prefetch), server
        dinyalakan sekali, lalu dimatikan dan cache dihapus sehingga GPU & disk bersih untuk model berikutnya.
        (scene, run) dijalankan paralel sebanyak args.parallel (= slot llama-server)."""
        launch = model.get("launch")
        # "parallel" di launch (mis. model 119B yang memenuhi VRAM) mengalahkan setelan form
        par = max(1, int((launch or {}).get("parallel", getattr(args, "parallel", 1))))
        proc = None
        cache_dir = model_cache_dir(model["name"]) if launch else None
        try:
            server = {}
            if launch:
                is_llama = launch.get("backend", "llama.cpp") == "llama.cpp"
                exe = ensure_llama_server(log) if is_llama else None
                if is_llama and model_path is None and can_prefetch(model):
                    model_path = fetch_gguf(model, log)
                port = _free_port()
                log(f"Memuat {model['name']} ke GPU ({par} slot paralel)...")
                proc, log_path, cache_dir = start_server(launch, port, model["name"], exe, model_path, par)
                wait_ready(proc, port, log_path, ready_timeout, log)
                server = {"base_url": f"http://127.0.0.1:{port}/v1"}
            payloads = []
            for cfg in expand_variants(model):
                cfg = {**cfg, **server}
                if launch:
                    cfg.setdefault("model", "default")
                jobs = [(si, sc, i) for si, sc in enumerate(scenes) for i in range(runs)]
                done = {}

                def _one(sc, i, cfg=cfg):
                    try:
                        return run_scene(cfg, sc, i, args)
                    except Exception as e:  # noqa
                        return {"scene": sc["id"], "run": i, "error": f"{type(e).__name__}: {e}"}

                # thread pekerja hanya memanggil model; progress dilaporkan dari thread sel (aman untuk UI marimo)
                with ThreadPoolExecutor(max_workers=par) as ex:
                    futs = {ex.submit(_one, sc, i): (si, sc, i) for si, sc, i in jobs}
                    for f in as_completed(futs):
                        si, sc, i = futs[f]
                        done[(si, i)] = res = f.result()
                        if step:
                            step(cfg["name"], sc["id"], i, res)
                results = [done[(si, i)] for si, _, i in jobs]
                p = {"name": cfg["name"], "model": cfg.get("model"), "variant_of": cfg.get("variant_of"),
                     "detector": DETECTOR, "results": results,
                     "settings": {"temperature": cfg.get("temperature", args.temperature),
                                  "top_p": cfg.get("top_p", args.top_p),
                                  "max_tokens": cfg.get("max_tokens", args.max_tokens),
                                  "extra": cfg.get("extra", {}), "launch": launch, "seed": args.seed,
                                  "parallel": par,
                                  "finished": datetime.now().isoformat(timespec="seconds")}}
                payloads.append(p)
                if on_variant_done:
                    on_variant_done(p)
            return payloads
        finally:
            if proc is not None:
                stop_server(proc)
            if cache_dir is not None and not keep_cache:
                shutil.rmtree(cache_dir, ignore_errors=True)


    class Prefetcher:
        """Unduh GGUF model berikutnya di latar belakang selagi model sekarang dites.
        depth = berapa model di depan yang boleh diunduh duluan; selalu dibatasi sisa disk."""

        def __init__(self, models, depth=1, margin_gb=8, log=print, keep_cache=False):
            self.models, self.depth, self.margin, self.log = models, max(0, int(depth)), margin_gb, log
            self.keep_cache = keep_cache
            self.pool = ThreadPoolExecutor(max_workers=1)  # satu per satu: Xet sudah memenuhi bandwidth
            self.futs = {}  # idx -> (future, ukuran GB)

        def schedule(self, current):
            for j in range(current + 1, min(len(self.models), current + 1 + self.depth)):
                m = self.models[j]
                if j in self.futs or not can_prefetch(m):
                    continue
                size = gguf_size_gb(m) or 40.0
                pending = sum(s for f, s in self.futs.values() if not f.done())
                free = disk_free_gb() or 0.0
                if free - pending < size + self.margin:
                    self.log(f"Prefetch {m['name']} ditunda: disk kosong {free} GB, butuh {size} GB + cadangan")
                    break  # dicoba lagi setelah model sekarang selesai dan cache-nya dihapus
                self.futs[j] = (self.pool.submit(fetch_gguf, m, lambda *_: None), size)

        def take(self, idx):
            """Path GGUF hasil prefetch untuk model idx (menunggu kalau belum selesai), atau None."""
            if idx not in self.futs:
                return None
            f, _ = self.futs.pop(idx)
            if not f.done():
                self.log(f"Menunggu unduhan {self.models[idx]['name']} selesai ...")
            try:
                return f.result()
            except Exception as e:  # noqa
                self.log(f"Prefetch {self.models[idx]['name']} gagal ({type(e).__name__}), unduh ulang ...")
                return None

        def close(self):
            self.pool.shutdown(wait=False, cancel_futures=True)
            if not self.keep_cache:
                for j, (f, _) in self.futs.items():
                    if f.done():
                        shutil.rmtree(model_cache_dir(self.models[j]["name"]), ignore_errors=True)
            self.futs.clear()


    def disk_free_gb(path=None):
        try:
            p = Path(path or WORK_DIR)
            while not p.exists() and p != p.parent:
                p = p.parent
            return round(shutil.disk_usage(p).free / 1e9, 1)
        except Exception:  # noqa
            return None


    # --------------------------------------------------------------------------------------
    # Penyimpanan hasil antar sesi
    # Molab menghapus file buatan kode saat notebook mati (hanya source, upload lewat file browser,
    # .env, dan mo.persistent_cache yang disimpan). Jadi: unduh results.json, atau set secret
    # HF_TOKEN + RP_HF_RESULTS_REPO supaya tiap varian otomatis diunggah ke dataset HF privat.
    # --------------------------------------------------------------------------------------
    def _safe(name):
        return re.sub(r"[^\w.@-]+", "_", name)


    def hf_results_enabled():
        return bool(os.environ.get("RP_HF_RESULTS_REPO") and os.environ.get("HF_TOKEN"))


    def hf_push_payload(payload):
        """Unggah satu payload varian ke results/<name>.json di dataset HF privat. Return path atau None."""
        if not hf_results_enabled():
            return None
        from huggingface_hub import HfApi

        repo = os.environ["RP_HF_RESULTS_REPO"]
        api = HfApi(token=os.environ["HF_TOKEN"])
        api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
        path = f"results/{_safe(payload['name'])}.json"
        api.upload_file(path_or_fileobj=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        path_in_repo=path, repo_id=repo, repo_type="dataset",
                        commit_message=f"rp_ab_test: {payload['name']}")
        return path


    def hf_pull_results():
        """Ambil semua results/*.json dari dataset HF. Return dict {name: payload}."""
        from huggingface_hub import HfApi, hf_hub_download

        repo, token = os.environ["RP_HF_RESULTS_REPO"], os.environ["HF_TOKEN"]
        files = [f for f in HfApi(token=token).list_repo_files(repo, repo_type="dataset")
                 if f.startswith("results/") and f.endswith(".json")]
        out = {}
        for f in files:
            p = hf_hub_download(repo, f, repo_type="dataset", token=token, force_download=True)
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            out[d["name"]] = d
        return out


    def parse_results_json(raw):
        """results.json hasil unduhan sebelumnya -> dict {name: payload}. Lempar ValueError kalau formatnya salah."""
        d = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        if not isinstance(d, dict) or not all(isinstance(v, dict) and "results" in v for v in d.values()):
            raise ValueError("bukan results.json dari notebook ini")
        return d


    # --------------------------------------------------------------------------------------
    # Statistik A/B: item berpasangan (scene, run, giliran) dengan seed sama antar model
    # --------------------------------------------------------------------------------------
    TIE_EPS = 0.25


    def item_scores(j):
        return {(r["scene"], r["run"], t["turn"]): t["analysis"]["score"]
                for r in j["results"] for t in r.get("turns", [])}


    def bootstrap_ci(vals, n=2000, seed=0, alpha=0.05):
        if len(vals) < 2:
            return None, None
        rng = random.Random(seed)
        k = len(vals)
        means = sorted(sum(rng.choice(vals) for _ in range(k)) / k for _ in range(n))
        return round(means[int(n * alpha / 2)], 2), round(means[int(n * (1 - alpha / 2)) - 1], 2)


    def sign_test_p(w, l):
        """Uji tanda dua sisi (seri dibuang)."""
        n = w + l
        if n == 0:
            return 1.0
        if n > 500:  # pendekatan normal dengan koreksi kontinuitas
            z = max(0.0, abs(w - l) - 1) / math.sqrt(n)
            return round(math.erfc(z / math.sqrt(2)), 4)
        k = min(w, l)
        return round(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


    def pairwise(data):
        """Bandingkan setiap pasangan model pada item yang sama. Return list dict."""
        scores = {n: item_scores(j) for n, j in data.items()}
        names = sorted(data)
        rows = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                keys = sorted(set(scores[a]) & set(scores[b]))
                if not keys:
                    continue
                d = [scores[a][k] - scores[b][k] for k in keys]
                w = sum(x > TIE_EPS for x in d)
                l = sum(x < -TIE_EPS for x in d)
                lo, hi = bootstrap_ci(d)
                p = sign_test_p(w, l)
                if lo is not None and p < 0.05 and (lo > 0 or hi < 0):
                    verdict = f"{a} lebih baik" if lo > 0 else f"{b} lebih baik"
                else:
                    verdict = "belum beda nyata"
                rows.append({"a": a, "b": b, "n": len(d), "win_a": w, "tie": len(d) - w - l, "win_b": l,
                             "mean_diff": round(statistics.mean(d), 2), "ci": (lo, hi), "p": p,
                             "verdict": verdict})
        return rows


    def win_rates(data, pairs=None):
        """Rata-rata (menang + seri/2) / n terhadap semua lawan."""
        pairs = pairwise(data) if pairs is None else pairs
        acc = {n: [] for n in data}
        for r in pairs:
            acc[r["a"]].append((r["win_a"] + r["tie"] / 2) / r["n"])
            acc[r["b"]].append((r["win_b"] + r["tie"] / 2) / r["n"])
        return {n: round(100 * statistics.mean(v)) if v else None for n, v in acc.items()}


    def label_for(i):
        """0 -> A, 25 -> Z, 26 -> AA (aman untuk >26 varian)."""
        s, i = "", i + 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s


    # --------------------------------------------------------------------------------------
    # Laporan
    # --------------------------------------------------------------------------------------


    def all_turns(j):
        for r in j["results"]:
            for t in r.get("turns", []):
                yield r["scene"], t


    def summarize(j):
        turns = list(all_turns(j))
        errors = sum(1 for r in j["results"] if "error" in r)
        if not turns:
            return {"avg": 0, "n": 0, "errors": errors}
        scores = [t["analysis"]["score"] for _, t in turns]
        flags = [f for _, t in turns for f in t["analysis"]["flags"]]
        lat = [t["latency"] for _, t in turns]
        tps = [t["tokens"] / t["latency"] for _, t in turns if t.get("tokens") and t["latency"] > 0]
        by_scene = {}
        for s, t in turns:
            by_scene.setdefault(s, []).append(t["analysis"]["score"])
        # degradasi sesi panjang: rata-rata sepertiga awal - sepertiga akhir
        drift = None
        ls = [t["analysis"]["score"] for s, t in turns if s == "long_session"]
        if len(ls) >= 6:
            k = max(1, len(ls) // 3)
            drift = round(statistics.mean(ls[:k]) - statistics.mean(ls[-k:]), 2)
        lo, hi = bootstrap_ci(scores)
        return {
            "avg": round(statistics.mean(scores), 2), "min": round(min(scores), 2), "n": len(scores),
            "ci": f"{lo}-{hi}" if lo is not None else "-",
            "pass": round(100 * sum(s >= 8 for s in scores) / len(scores)),
            "refusal": flags.count("REFUSAL"), "lecture": flags.count("LECTURE"),
            "frag": flags.count("FRAGMENTED"), "loop": flags.count("LOOP"),
            "trunc": flags.count("TRUNCATED"), "leak": flags.count("THINK_LEAK"), "empty": flags.count("EMPTY"),
            "lat": round(statistics.mean(lat), 1), "tps": round(statistics.mean(tps), 1) if tps else None,
            "by_scene": {k: round(statistics.mean(v), 2) for k, v in by_scene.items()},
            "drift": drift, "errors": errors,
        }


    def md_table(rows, headers):
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        return "\n".join(out)


    def build_report(data, scene_ids, seed=7):
        """Return (report_md, judge_packet_md, key_dict). Murni: tanpa baca/tulis file."""
        sums = {n: summarize(j) for n, j in data.items()}
        pairs = pairwise(data)
        wr = win_rates(data, pairs)
        ranked = sorted(sums, key=lambda n: -sums[n]["avg"])
        lines = [f"# Hasil A/B test otomatis  ({datetime.now():%Y-%m-%d %H:%M})\n",
                 f"Detektor bahasa: **{next(iter(data.values())).get('detector')}**  |  skor otomatis 0-10 "
                 "(format 3, Jerman 2, Inggris 1, Indonesia 2, tanpa fragmen 1, higiene 1)\n",
                 "## Peringkat\n"]
        lines.append(md_table(
            [[i + 1, n, s["avg"], s.get("ci", "-"), f"{wr.get(n)}%" if wr.get(n) is not None else "-", s.get("min"),
              f"{s.get('pass')}%", s.get("n"), s.get("refusal"), s.get("lecture"), s.get("frag"), s.get("loop"),
              s.get("trunc"), s.get("leak"), s.get("drift"), s.get("lat"), s.get("tps"), s["errors"]]
             for i, (n, s) in enumerate((n, sums[n]) for n in ranked)],
            ["#", "model", "rata2", "CI95", "win-rate", "min", "lulus>=8", "n item", "refusal", "ceramah", "fragmen",
             "loop", "terpotong", "bocor-think", "drift(awal-akhir)", "detik/resp", "tok/s", "error"]))
        if pairs:
            lines += ["\n## Head-to-head (item berpasangan: scene, run, giliran sama)\n",
                      md_table([[r["a"], r["b"], r["n"], r["win_a"], r["tie"], r["win_b"], r["mean_diff"],
                                 f"{r['ci'][0]} s/d {r['ci'][1]}" if r["ci"][0] is not None else "-", r["p"],
                                 r["verdict"]] for r in sorted(pairs, key=lambda r: -abs(r["mean_diff"]))],
                               ["A", "B", "n", "A menang", "seri", "B menang", "selisih rata2 (A-B)", "CI95 selisih",
                                "p (uji tanda)", "kesimpulan"])]
        scenes = sorted({k for s in sums.values() for k in s.get("by_scene", {})})
        lines += ["\n## Rata-rata per scene\n",
                  md_table([[n] + [sums[n].get("by_scene", {}).get(sc, "-") for sc in scenes] for n in ranked],
                           ["model"] + scenes)]
        lines.append("\n## Catatan\n- `drift` positif = kualitas turun di akhir sesi panjang.\n"
                     f"- Seri = selisih skor <= {TIE_EPS}. `win-rate` = (menang + seri/2) / n, dirata-rata atas semua lawan.\n"
                     "- 'beda nyata' butuh p < 0.05 DAN CI95 selisih tidak melewati 0. Dengan 1 run per scene, "
                     "n terlalu kecil: pakai >= 3 run.\n"
                     "- Skor otomatis hanya mengukur format/bahasa/refusal. Kealamian Indonesia, tata bahasa Jerman, "
                     "dan kualitas prosa dinilai lewat judge_packet.md.")
        report = "\n".join(lines)

        # ---- judge packet (anonim, urutan diacak per scene)
        rng = random.Random(seed)
        names = sorted(data)
        labels = {n: label_for(i) for i, n in enumerate(rng.sample(names, len(names)))}
        pk = ["# Paket penilaian (anonim)\n",
              "Kamu penilai. Setiap output di bawah adalah roleplay: narasi Indonesia, dialog Jerman, terjemahan Inggris "
              "dalam kurung. Nilai tiap (scene, model) dengan skala 0-2 untuk kriteria berikut:\n"]
        pk += [f"- `{k}`: {v}" for k, v in JUDGE_CRITERIA.items()]
        pk.append("\nBalas HANYA dengan JSON array, satu objek per (label, scene):\n"
                  '```json\n[{"label":"A","scene":"lotte_basic","scores":{"id_natural":2,"de_grammar":1,'
                  '"consistency":2,"prose":1,"no_moralizing":2},"note":"singkat"}]\n```\n'
                  "Setelah JSON, beri peringkat akhir + alasan 2-3 kalimat per model. Abaikan nama model; label sudah diacak.\n")
        for sc in scene_ids:
            pk.append(f"\n---\n## Scene: {sc}\n")
            order = names[:]
            rng.shuffle(order)
            for n in order:
                runs = [r for r in data[n]["results"] if r.get("scene") == sc and r.get("turns")]
                if not runs:
                    continue
                tl = runs[0]["turns"]
                pick = tl if len(tl) <= 3 else [tl[0], tl[len(tl) // 2], tl[-1]]
                pk.append(f"\n### Model {labels[n]}\n")
                for t in pick:
                    # skor/flag otomatis sengaja tidak ditampilkan supaya penilaian tetap buta
                    pk.append(f"**Giliran {t['turn']}**\n\n```text\n{t['output']}\n```\n")
        return report, "\n".join(pk), labels


    def final_table(data, key, judge_raw, auto_weight=0.5):
        """Gabungkan skor otomatis + penilaian Claude. Return markdown tabel."""
        inv = {v: k for k, v in key.items()}
        m = re.search(r"\[.*\]", judge_raw, flags=re.S)
        judged = json.loads(m.group(0)) if m else []
        per = {}
        for j in judged:
            name = inv.get(j["label"])
            if name:
                per.setdefault(name, []).append(sum(j["scores"].get(k, 0) for k in JUDGE_CRITERIA))
        rows = []
        for n in data:
            auto = summarize(data[n])["avg"]
            jd = round(statistics.mean(per[n]), 2) if per.get(n) else None
            final = round(auto_weight * auto + (1 - auto_weight) * jd, 2) if jd is not None else auto
            rows.append((n, auto, jd, final))
        rows.sort(key=lambda r: -r[3])
        return (md_table([[i + 1, *r] for i, r in enumerate(rows)],
                         ["#", "model", "skor otomatis", "skor Claude (0-10)", "FINAL"])
                + f"\n\nFINAL = {auto_weight:.0%} otomatis + {1 - auto_weight:.0%} penilaian Claude")
    return (argparse, fnmatch, json, math, os, random, re, shlex, shutil, signal, socket, statistics, subprocess, sys, tarfile, time, datetime, Path, requests, DETECTOR, detect, SYSTEM_PROMPT, SCENES, JUDGE_CRITERIA, QUOTE_MAP, Q_RE, PAREN_RE, SENT_SPLIT, ELLIPSIS_EDGE, REFUSAL_RE, LECTURE_RE, wc, clean_text, analyze, MOCK_OUTPUTS, chat, run_scene, LLAMA_DIR, WORK_DIR, gpu_info, pick_llama_assets, ensure_llama_server, build_cmd, start_server, wait_ready, stop_server, model_cache_dir, can_prefetch, gguf_size_gb, fetch_gguf, Prefetcher, expand_variants, execute_model, disk_free_gb, hf_results_enabled, hf_push_payload, hf_pull_results, parse_results_json, item_scores, bootstrap_ci, sign_test_p, pairwise, win_rates, label_for, all_turns, summarize, md_table, build_report, final_table,)


@app.cell(hide_code=True)
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo, DETECTOR):
    mo.md(f"""
    # A/B test model roleplay (Indonesia + Jerman + Inggris)

    Versi **Molab** dari `rp_ab_test.py`. Detektor bahasa aktif: **{DETECTOR}**.

    **Fakta Molab yang relevan** (docs marimo, 2026): notebook gratis di sandbox CoreWeave, 4 CPU + 32GB RAM,
    GPU opsional RTX Pro 6000 Blackwell 96GB VRAM. Sesi maksimal **12 jam**, mati otomatis setelah **90 menit idle**.
    Saat notebook mati, yang disimpan hanya source, file yang diunggah lewat file browser, `.env` (panel Secrets),
    dan `mo.persistent_cache`. File buatan kode, termasuk unduhan model dan hasil tes, **dihapus**.

    **Cara kerja**
    1. Klik tombol **spesifikasi notebook** di header Molab dan **nyalakan GPU**. Di panel **Secrets** isi
       `HF_TOKEN` (token *read* dari huggingface.co/settings/tokens) supaya unduhan tidak kena batas akses anonim.
    2. Pilih **preset** (Batch 1, Batch 2, Qwen3.8, Semua) atau tulis JSON sendiri. Model dengan `"launch"` + `"hf_file"`
       diunduh lewat `huggingface_hub` (Xet mode high-performance), dimuat di llama.cpp, dites, lalu dimatikan dan
       file-nya dihapus. Selagi satu model dites, model berikutnya sudah diunduh di latar belakang (atur di form,
       dibatasi sisa disk). Model dengan `"base_url"` memakai API luar (key lewat **Secrets**, rujuk dengan `api_key_env`).
    3. `"variants"` di satu model = beberapa setelan sampler/prompt yang dites tanpa memuat ulang model.
       Tiap varian muncul sebagai peserta A/B tersendiri (`nama@suffix`).
    4. Pakai **>= 3 run per scene** supaya tabel head-to-head punya cukup data untuk uji tanda + CI95.
    5. Hasil antar sesi: unduh `results.json` lalu unggah lagi di bagian 0, **atau** isi secret `HF_TOKEN` +
       `RP_HF_RESULTS_REPO` (mis. `namamu/rp-ab-results`) supaya tiap varian otomatis diunggah ke dataset HF privat.
    6. Unduh `judge_packet.md`, kirim ke Claude, lalu tempel JSON penilaian di bagian akhir.

    Coba dulu dengan **model demo** (`"base_url": "mock"`) untuk memastikan semuanya jalan.
    """)
    return


@app.cell(hide_code=True)
def _(disk_free_gb, gpu_info, hf_results_enabled, mo):
    _g = gpu_info()
    _disk = disk_free_gb()
    _disk_txt = f"Disk kosong di folder kerja: **{_disk} GB** (Q6_K 31B ~25GB, Q8_0 ~33GB per model)." \
        if _disk is not None else "Disk kosong: tidak terbaca."
    _hf_txt = ("Auto-unggah hasil ke HF: **aktif**." if hf_results_enabled() else
               "Auto-unggah hasil ke HF: mati (isi secret `HF_TOKEN` + `RP_HF_RESULTS_REPO` untuk menyalakan).")
    if _g["ok"]:
        _cuda = ".".join(map(str, _g["cuda"])) if _g["cuda"] else "?"
        _msg = f"**GPU aktif:** {_g['name']} (CUDA driver {_cuda})"
    else:
        _msg = ("**GPU belum aktif.** Nyalakan lewat tombol spesifikasi notebook di header Molab, lalu restart sesi. "
                "Tanpa GPU, model 27-31B akan sangat lambat.")
    mo.callout(mo.md(f"{_msg}\n\n{_disk_txt}\n\n{_hf_txt}"), kind="success" if _g["ok"] else "warn")
    return


@app.cell(hide_code=True)
def _(json, mo):
    # Semua repo & nama file dicek lewat API Hugging Face (26 Nov 2026): ada, tidak gated, ukuran Q6_K tertera.
    _QWEN = {"max_tokens": 1500, "extra": {"chat_template_kwargs": {"enable_thinking": False}}}

    def _gg(name, repo, file, gb, note, **kw):
        return {"name": name, "launch": {"backend": "llama.cpp", "hf": repo, "hf_file": file},
                "note": f"{note} | Q6_K {gb} GB", **kw}

    _B1 = [
        _gg("gemma4-31b-ara-heretic", "mradermacher/gemma-4-31B-it-ARA-heretic-GGUF",
            "gemma-4-31B-it-ARA-heretic.Q6_K.gguf", 25.2, "pembanding dasar Gemma heretic"),
        _gg("qwen38-27b-abliterated", "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
            "Huihui-Qwen3.8-27B-abliterated-Q6_K.gguf", 22.4, "keluarga Qwen, abliterated", **_QWEN),
        _gg("gembrain-31b-heretic", "llmfan46/Gemma-4-Gembrain-31B-it-uncensored-heretic-GGUF",
            "Gemma-4-Gembrain-31B-it-uncensored-heretic-Q6_K.gguf", 25.2, "merge Gembrain"),
        _gg("dark-gemistry-31b", "ReadyArt/Nimbz_Gemma-4-Dark-Gemistry-31B-GGUF",
            "Gemma-4-Dark-Gemistry-31B-Q6_K.gguf", 26.4, "merge khusus roleplay"),
        _gg("gemstrike-31b", "UltimateIntent/GemStrike-31B-GGUF", "GemStrike-31B-Q6_K.gguf", 25.2,
            "LoRA di atas basis abliterated"),
    ]
    _B2 = [
        _gg("garnet-v2-31b-heretic", "llmfan46/Gemma-4-Garnet-V2-31B-it-ultra-uncensored-heretic-GGUF",
            "Gemma-4-Garnet-V2-31B-it-ultra-uncensored-heretic-Q6_K.gguf", 25.2, "fine-tune prosa"),
        _gg("sphinsikus-chronist-31b", "mradermacher/Gemma-4-Sphinsikus-Chronist-31B-GGUF",
            "Gemma-4-Sphinsikus-Chronist-31B.Q6_K.gguf", 25.2, "konsistensi karakter"),
        _gg("isometry-rp-31b", "mradermacher/Gemma-4-31B-Isometry-RP-GGUF",
            "Gemma-4-31B-Isometry-RP.Q6_K.gguf", 26.4, "anti-melantur di generasi panjang"),
        _gg("gembrain-x-31b", "Nimbz/Gemma-4-Gembrain-X-31B-GGUF", "static/Gembrain-X-31B-Q6_K.gguf", 25.2,
            "Gembrain versi baru"),
        _gg("gemma4-31b-heretic-llmfan46", "llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF",
            "gemma-4-31B-it-uncensored-heretic-Q6_K.gguf", 25.2, "heretic versi llmfan46"),
    ]
    _QW = [
        _B1[1],
        _gg("qwen38-27b-heretic", "llmfan46/Qwen3.8-27B-Uncensored-Heretic-Native-MTP-Preserved-GGUF",
            "Qwen3.8-27B-Ultra-Uncensored-Heretic-Native-MTP-Preserved-Q6_K.gguf", 22.8,
            "heretic llmfan46 (lapisan MTP disertakan)", **_QWEN),
        _gg("qwen38-27b-heretic-ara", "mradermacher/Qwen3.8-27B-heretic-ara-GGUF",
            "Qwen3.8-27B-heretic-ara.Q6_K.gguf", 22.4, "heretic ARA, pasangan gemma ARA", **_QWEN),
        _gg("qwen38-27b-heretic-abliterated-ml", "0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF",
            "RVN-Q6_K-multilingual.gguf", 22.1, "heretic+abliterated, varian 'multilingual'", **_QWEN),
        _gg("qwen38-40b-heretic", "mradermacher/Qwen3.8-40B-Ultimate-Heretic-Uncensored-GGUF",
            "Qwen3.8-40B-Ultimate-Heretic-Uncensored.Q6_K.gguf", 32.4, "upscale 40B DavidAU (statis)", **_QWEN),
        _gg("qwen38-40b-heretic-neo", "DavidAU/Qwen3.8-40B-Ultimate-Heretic-Uncensored-Turbo-Brilliance-50x-AGI-NEO-GGUF",
            "Qwen3.8-40B-Ultimate-Heretic-Uncn-Turbo-Brilli-50x-AGI-NEO-Q6_K.gguf", 32.1,
            "40B DavidAU, kuantisasi imatrix NEO", **_QWEN),
    ]
    _CMP = [
        _gg("mistral-small-3.2-24b-heretic", "llmfan46/Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-GGUF",
            "Mistral-Small-3.2-24B-Instruct-2506-ultra-uncensored-heretic-Q6_K.gguf", 19.3, "Mistral lama, ringan"),
        _gg("qwen-sea-lion-v5-27b", "aisingapore/Qwen-SEA-LION-v5-27B-GGUF", "Qwen-SEA-LION-v5-27B-Q6_K.gguf", 22.4,
            "tolok ukur bahasa Indonesia, BUKAN uncensored (scene gelap bisa menolak)", **_QWEN),
        # 119B MoE: Q4_K_M 72.2GB -> sisa VRAM sedikit, jadi 1 slot dan ctx 16k (8k terlalu pendek untuk long_session)
        {"name": "mistral-small-4-119b-heretic",
         "launch": {"backend": "llama.cpp", "hf": "Rioky/Mistral-Small-4-119B-2603-heretic-gguf",
                    "hf_file": "Mistral-Small-4-119B-2603-heretic-Q4_K_M.gguf", "ctx": 16384, "parallel": 1},
         "note": "119B MoE heretic | Q4_K_M 72.2 GB, butuh disk >= 80GB"},
    ]
    _DEMO = [
        {"name": "demo-bagus", "base_url": "mock", "model": "good",
         "variants": [{"suffix": "t0.7", "temperature": 0.7}, {"suffix": "t1.0", "temperature": 1.0}]},
        {"name": "demo-terpecah", "base_url": "mock", "model": "frag"},
        {"name": "demo-melantur", "base_url": "mock", "model": "drift"},
    ]
    _all = {m["name"]: m for m in _B1 + _B2 + _QW + _CMP}
    PRESETS = {
        "Demo (tanpa GPU)": _DEMO,
        "Batch 1 (5 model)": _B1,
        "Batch 2 (5 merge Gemma)": _B2,
        "Qwen3.8 27B + 40B (6 model)": _QW,
        "Pembanding: Mistral + SEA-LION (3 model)": _CMP,
        f"Semua ({len(_all)} model)": list(_all.values()),
    }

    def preset_json(key):
        rows = ",\n".join("  " + json.dumps(m, ensure_ascii=False) for m in PRESETS[key])
        return "[\n" + rows + "\n]"

    preset = mo.ui.dropdown(options=list(PRESETS), value="Demo (tanpa GPU)", label="Preset daftar model")
    preset
    return preset, preset_json


@app.cell(hide_code=True)
def _(mo, preset, preset_json):
    GPU_EXAMPLE = """[
  {"name": "gemma4-31b-heretic",
   "launch": {"backend": "llama.cpp", "hf": "llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF",
              "hf_file": "gemma-4-31B-it-uncensored-heretic-Q6_K.gguf", "ctx": 32768},
   "variants": [{"suffix": "t0.8"},
                {"suffix": "vendor", "temperature": 1.0, "top_p": 0.95, "extra": {"top_k": 64}}]},
  {"name": "gembrain-31b-heretic",
   "launch": {"backend": "llama.cpp", "hf": "llmfan46/Gemma-4-Gembrain-31B-it-uncensored-heretic-GGUF",
              "hf_file": "Gemma-4-Gembrain-31B-it-uncensored-heretic-Q6_K.gguf", "ctx": 32768},
   "variants": [{"suffix": "t0.8"},
                {"suffix": "vendor", "temperature": 1.0, "top_p": 0.95, "extra": {"top_k": 64}}]},
  {"name": "qwen38-27b-abliterated",
   "launch": {"backend": "llama.cpp", "hf": "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF",
              "hf_file": "Huihui-Qwen3.8-27B-abliterated-Q6_K.gguf", "ctx": 32768},
   "max_tokens": 1500, "extra": {"chat_template_kwargs": {"enable_thinking": false}},
   "variants": [{"suffix": "t0.8"},
                {"suffix": "vendor", "temperature": 0.7, "top_p": 0.8, "extra": {"top_k": 20, "min_p": 0}}]},
  {"name": "gemma4-31b-vllm",
   "launch": {"backend": "command", "cmd": "vllm serve google/gemma-4-31B-it --port {port} --max-model-len 32768"},
   "model": "google/gemma-4-31B-it"},
  {"name": "api-featherless",
   "base_url": "https://api.featherless.ai/v1", "model": "google/gemma-4-31B-it", "api_key_env": "FEATHERLESS_API_KEY"}
]"""
    models_json = mo.ui.text_area(value=preset_json(preset.value), rows=16, full_width=True, label="")
    mo.vstack([
        mo.md("### 1. Daftar model (JSON)\nGanti preset di atas akan menimpa isi kotak ini. "
              "Pilih model yang dijalankan di bagian 2."),
        models_json,
        mo.accordion({"Contoh untuk GPU Molab (salin ke kotak di atas)": mo.md(
            "```json\n" + GPU_EXAMPLE + "\n```\n"
            "- `hf_file` = nama file GGUF persis (sudah dicek ada di repo, Nov 2026). Ukuran: Gemma Q6_K 25.2GB, "
            "Q8_0 32.6GB; Qwen3.8 Q6_K 22.4GB, Q8_0 29.0GB. Repo Qwen juga punya `Q6_K_L`, `UD-*`, `-mtp`, "
            "jadi nama persis lebih aman dari tag `:Q6_K`.\n"
            "- File mmproj (vision) tidak diunduh (`--no-mmproj`). Set `\"mmproj\": true` di `launch` kalau perlu.\n"
            "- `ctx` 32768: sesi panjang 10 giliran x ~1200 token bisa melewati 16k. VRAM 96GB cukup.\n"
            "- Varian `vendor` = sampler rekomendasi umum keluarga model (Gemma: temp 1.0/top_k 64; Qwen non-thinking: "
            "temp 0.7/top_p 0.8/top_k 20). Varian tanpa override memakai slider di form.\n"
            "- Backend `command` memakai perintah apa pun yang kamu pasang sendiri (mis. vLLM); `{port}` diisi otomatis.\n"
            "- Qwen: `enable_thinking=false` supaya token tidak habis untuk berpikir.")}),
    ])
    return (models_json,)


@app.cell(hide_code=True)
def _(SCENES, json, mo, models_json):
    try:
        models_cfg = json.loads(models_json.value)
        assert isinstance(models_cfg, list) and models_cfg, "harus array JSON berisi minimal 1 model"
        for _m in models_cfg:
            assert "name" in _m, "tiap model butuh 'name'"
            assert "launch" in _m or "base_url" in _m, f"model '{_m['name']}' butuh 'launch' atau 'base_url'"
            assert isinstance(_m.get("variants", []), list), f"'variants' di '{_m['name']}' harus array"
        assert len({_m["name"] for _m in models_cfg}) == len(models_cfg), "nama model harus unik"
    except Exception as _e:
        mo.stop(True, mo.callout(mo.md(f"**JSON model tidak valid:** `{_e}`"), kind="danger"))
    _names = [_m["name"] for _m in models_cfg]
    _scene_ids = [_s["id"] for _s in SCENES]
    form = (
        mo.md("""
        ### 2. Pengaturan tes

        {models}

        {scenes}

        {runs}

        {temperature}

        {max_tokens}

        {parallel}

        {prefetch}

        {keep_cache}
        """).batch(
            models=mo.ui.multiselect(options=_names, value=_names, label="Model yang dijalankan (urut sesuai daftar)",
                                     full_width=True),
            scenes=mo.ui.multiselect(options=_scene_ids, value=[_i for _i in _scene_ids if _i != "long_session"],
                                     label="Scene (long_session = 10 giliran, paling lama)", full_width=True),
            runs=mo.ui.slider(1, 10, value=3, step=1, label="Run per scene (seed beda tiap run, sama antar model)",
                              show_value=True),
            temperature=mo.ui.slider(0.0, 1.5, value=0.8, step=0.05, label="Temperature", show_value=True),
            max_tokens=mo.ui.number(start=300, stop=8000, step=100, value=1200, label="max_tokens"),
            parallel=mo.ui.slider(1, 8, value=3, step=1, show_value=True,
                                  label="Slot paralel per model (llama-server -np; KV cache naik sebanding)"),
            prefetch=mo.ui.slider(0, 15, value=1, step=1, show_value=True,
                                  label="Unduh model berikutnya di muka (0 = tidak; 15 = semua, kalau disk cukup)"),
            keep_cache=mo.ui.checkbox(value=False, label="Simpan unduhan model setelah selesai (butuh disk besar)"),
        ).form(submit_button_label="Jalankan tes", bordered=False)
    )
    form
    return form, models_cfg


@app.cell(hide_code=True)
def _(mo):
    get_results, set_results = mo.state({})
    clear_btn = mo.ui.button(label="Kosongkan semua hasil", kind="danger", on_click=lambda _: set_results({}))
    return clear_btn, get_results, set_results


@app.cell(hide_code=True)
def _(hf_results_enabled, mo):
    prev_file = mo.ui.file(filetypes=[".json"], label="Unggah results.json sesi sebelumnya", kind="area")
    hf_pull_btn = mo.ui.run_button(label="Ambil semua hasil dari dataset HF", disabled=not hf_results_enabled())
    mo.vstack([mo.md("### 0. Lanjutkan dari hasil sebelumnya (opsional)\n"
                     "Hasil digabung per nama varian; nama yang sama ditimpa versi terbaru."),
               mo.hstack([prev_file, hf_pull_btn], justify="start", align="center")])
    return hf_pull_btn, prev_file


@app.cell(hide_code=True)
def _(hf_pull_btn, hf_pull_results, mo, parse_results_json, prev_file, set_results):
    _msgs = []
    if prev_file.value:
        try:
            _loaded = parse_results_json(prev_file.contents())
            set_results(lambda _prev: {**_prev, **_loaded})
            _msgs.append(f"Dimuat dari file: {len(_loaded)} varian ({', '.join(sorted(_loaded))}).")
        except Exception as _e:
            _msgs.append(f"**Gagal membaca file:** `{_e}`")
    if hf_pull_btn.value:
        try:
            _pulled = hf_pull_results()
            set_results(lambda _prev: {**_prev, **_pulled})
            _msgs.append(f"Dimuat dari HF: {len(_pulled)} varian ({', '.join(sorted(_pulled))}).")
        except Exception as _e:
            _msgs.append(f"**Gagal ambil dari HF:** `{type(_e).__name__}: {_e}`")
    mo.md("\n\n".join(_msgs)) if _msgs else None
    return


@app.cell(hide_code=True)
def _(Path, Prefetcher, SCENES, argparse, can_prefetch, execute_model, expand_variants, fetch_gguf, form,
      hf_push_payload, json, mo, model_cache_dir, models_cfg, set_results, shutil):
    mo.stop(form.value is None, mo.md("_Pilih model & scene lalu tekan **Jalankan tes**._"))
    _cfg = form.value
    _sel = [_m for _m in models_cfg if _m["name"] in _cfg["models"]]
    mo.stop(not _sel, mo.md("**Pilih minimal satu model.**"))
    _by_id = {_s["id"]: _s for _s in SCENES}
    _scenes = [_by_id[_i] for _i in _cfg["scenes"]]
    mo.stop(not _scenes, mo.md("**Pilih minimal satu scene.**"))
    _ns = argparse.Namespace(temperature=_cfg["temperature"], top_p=0.95,
                             max_tokens=int(_cfg["max_tokens"]), timeout=900, seed=1000,
                             parallel=int(_cfg["parallel"]))
    _runs = int(_cfg["runs"])
    _keep = bool(_cfg["keep_cache"])
    _per_variant = len(_scenes) * _runs
    _total = sum(len(expand_variants(_m)) for _m in _sel) * _per_variant
    _new, batch_log = {}, []

    def _save_local():
        try:
            Path("rp_results_partial.json").write_text(json.dumps(_new, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _variant_done(p):
        # simpan segera per varian: kalau sesi mati di tengah, varian yang selesai tidak hilang
        _new[p["name"]] = p
        _save_local()
        try:
            _path = hf_push_payload(p)
            if _path:
                batch_log.append(f"Diunggah ke HF: {_path}")
        except Exception as _e:
            batch_log.append(f"Gagal unggah HF ({p['name']}): {type(_e).__name__}: {_e}")
        # tampilkan hasil yang sudah selesai walau model berikutnya belum jalan
        set_results(lambda _prev, _p=p: {**_prev, _p["name"]: _p})

    with mo.status.progress_bar(total=_total, title="Menjalankan tes") as _bar:
        _pf = Prefetcher(_sel, depth=int(_cfg["prefetch"]), keep_cache=_keep,
                         log=lambda msg: batch_log.append(str(msg)))
        try:
            for _idx, _m in enumerate(_sel):
                _done = [0]
                _n_var = len(expand_variants(_m))

                def _log(msg, _name=_m["name"]):
                    _bar.update(increment=0, title=_name, subtitle=str(msg)[:200])

                def _step(name, scene, i, res, _done=_done):
                    _done[0] += 1
                    _bar.update(increment=1, title=name, subtitle=f"{scene} run{i + 1}")

                try:
                    # 1) GGUF model ini: dari prefetch, atau unduh sekarang (sebelum prefetch berikutnya mulai,
                    #    supaya dua unduhan tidak berebut bandwidth)
                    _path = _pf.take(_idx)
                    if _path is None and can_prefetch(_m):
                        _path = fetch_gguf(_m, _log)
                    # 2) mulai unduh model berikutnya di latar belakang selagi model ini dites
                    _pf.schedule(_idx)
                    # 3) tes
                    _payloads = execute_model(_m, _scenes, _runs, _ns, log=_log, step=_step, keep_cache=_keep,
                                              ready_timeout=3600, on_variant_done=_variant_done, model_path=_path)
                    batch_log.append(f"OK: {', '.join(_p['name'] for _p in _payloads)}")
                except Exception as _e:
                    _msg = f"{type(_e).__name__}: {_e}"
                    batch_log.append(f"GAGAL: {_m['name']} -> {_msg[:300]}")
                    _bar.update(increment=max(0, _n_var * _per_variant - _done[0]))
                    if not _keep:
                        shutil.rmtree(model_cache_dir(_m["name"]), ignore_errors=True)
        finally:
            _pf.close()
    mo.md("**Selesai.**\n\n" + "\n".join(f"- {_x}" for _x in batch_log))
    return (batch_log,)


@app.cell(hide_code=True)
def _(SCENES, batch_log, build_report, clear_btn, get_results, json, mo):
    _dep = batch_log  # memastikan sel ini jalan setelah sel tes selesai
    results = get_results()
    mo.stop(not results, mo.md("_Belum ada hasil._"))
    _present = [_s["id"] for _s in SCENES
                if any(_r.get("scene") == _s["id"] for _j in results.values() for _r in _j["results"])]
    report_md, packet_md, label_key = build_report(results, _present)
    _dl_packet = mo.download(data=packet_md.encode("utf-8"), filename="judge_packet.md",
                             mimetype="text/markdown", label="Unduh judge_packet.md (kirim ke Claude)")
    _dl_report = mo.download(data=report_md.encode("utf-8"), filename="report.md",
                             mimetype="text/markdown", label="Unduh report.md")
    _dl_json = mo.download(data=json.dumps(results, ensure_ascii=False, indent=2).encode("utf-8"),
                           filename="results.json", mimetype="application/json", label="Unduh results.json")
    mo.vstack([
        mo.md("### 3. Hasil skor otomatis"),
        mo.md(report_md),
        mo.hstack([_dl_packet, _dl_report, _dl_json, clear_btn], justify="start"),
        mo.accordion({"Kunci label (buka SETELAH dinilai, supaya tetap buta)":
                      mo.md("```json\n" + json.dumps(label_key, indent=2) + "\n```")}),
    ])
    return label_key, packet_md, report_md, results


@app.cell(hide_code=True)
def _(mo, results):
    _scenes = sorted({_r["scene"] for _j in results.values() for _r in _j["results"]})
    pick_model = mo.ui.dropdown(options=list(results), value=list(results)[0], label="Model")
    pick_scene = mo.ui.dropdown(options=_scenes, value=_scenes[0], label="Scene")
    mo.vstack([mo.md("### 4. Lihat output mentah"), mo.hstack([pick_model, pick_scene], justify="start")])
    return pick_model, pick_scene


@app.cell(hide_code=True)
def _(mo, pick_model, pick_scene, results):
    _runs = [_r for _r in results[pick_model.value]["results"] if _r.get("scene") == pick_scene.value]
    _items = []
    if not _runs:
        _items.append(mo.md("_Tidak ada hasil untuk scene ini._"))
    elif "error" in _runs[0]:
        _items.append(mo.md(f"**Error:** `{_runs[0]['error']}`"))
    else:
        for _t in _runs[0]["turns"]:
            _a = _t["analysis"]
            _items.append(mo.md(
                f"**Giliran {_t['turn']}** — skor {_a['score']} — flag: {', '.join(_a['flags']) or '-'} "
                f"(kutip {_a['n_quotes']}, berpasangan {_a['paired']}, Jerman {_a['de_frac']}, Indonesia {_a['id_frac']})\n\n"
                f"```text\n{_t['output']}\n```"))
    mo.vstack(_items)
    return


@app.cell(hide_code=True)
def _(mo):
    judge_form = (
        mo.md("""
        ### 5. Peringkat akhir (setelah dinilai Claude)

        Tempel JSON penilaian dari Claude:

        {judge}

        {weight}
        """).batch(
            judge=mo.ui.text_area(rows=10, full_width=True, label=""),
            weight=mo.ui.slider(0.0, 1.0, value=0.5, step=0.1, label="Bobot skor otomatis", show_value=True),
        ).form(submit_button_label="Hitung peringkat akhir", bordered=False)
    )
    judge_form
    return (judge_form,)


@app.cell(hide_code=True)
def _(final_table, judge_form, label_key, mo, results):
    mo.stop(judge_form.value is None, mo.md("_Tempel JSON penilaian lalu tekan tombol._"))
    try:
        _tbl = final_table(results, label_key, judge_form.value["judge"], judge_form.value["weight"])
    except Exception as _e:
        mo.stop(True, mo.md(f"**Gagal membaca JSON penilaian:** `{_e}`"))
    mo.md(_tbl)
    return


if __name__ == "__main__":
    app.run()
