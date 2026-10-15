# -*- coding: utf-8 -*-
"""
i18n.py — 中英双语字符串表。

界面语言通过顶部页签栏右侧的语言胶囊切换，所有文案集中在这里维护。
每个语言界面只显示单一语言，不做中英混排。
"""

LANG = "zh"

S = {
    # ------------------------------------------------------------------ 中文
    "zh": {
        "window_title": "FO76 → FO4 Texture Converter",

        # 页签
        "tab_basic": "基础",
        "tab_options": "选项",
        "tab_tex": "贴图",
        "tab_mat": "材质",
        "tab_out": "输出",

        # 分组卡片
        "gb_dirs": "目录",
        "gb_mode": "转换模式",
        "gb_opts": "常用选项",

        # 目录
        "lbl_in": "FO76 输入",
        "lbl_out": "FO4 输出",
        "ph_in": "拖入 Fallout 76 资源文件夹，或点击选择",
        "ph_out": "选择 Fallout 4 输出目录（不能与输入相同）",
        "btn_browse": "选择…",
        "pick_in": "选择 Fallout 76 输入目录",
        "pick_out": "选择 Fallout 4 输出目录",

        # 转换模式
        "mode_high": "高质量",
        "mode_balanced": "平衡",
        "mode_space": "节省空间",

        # 常用选项
        "cb_keep": "保留目录结构",
        "cb_mip": "自动生成 Mipmap",
        "cb_gens": "自动生成 FO4 _s.dds（_l + _r → _s）",
        "cb_metal": "自动金属补偿（默认关闭）",
        "cb_glow_l": "从 _l Alpha 生成 Glow（默认关闭）",

        # 贴图页
        "lbl_diffuse": "Diffuse 格式",
        "lbl_normal": "Normal 格式（恒为 BC5）",
        "lbl_spec": "Specular 格式",
        "lbl_glow": "Glow 格式",
        "note_tex": "切换「基础」页的转换模式会重置这里的格式预设。",

        # 材质页
        "cb_invert": "反转法线 Green Channel（默认关闭）",
        "cb_ao": "AO → Diffuse（默认关闭）",
        "lbl_ao": "AO 强度",
        "lbl_metal": "金属强度",
        "note_mat": "SSS（_l.B）默认忽略：FO4 普通 BGSM 无一一对应参数，"
                    "不会写入 _s，也不会破坏 Diffuse。",

        # 输出页
        "cb_overwrite": "覆盖已存在的输出文件",
        "cb_log": "生成日志 conversion_log.txt",
        "cb_report": "生成报告 conversion_report.txt",
        "cb_bgsm": "转换 BGSM / BGEM（实验性）",

        # 操作按钮
        "btn_dry": "仅分析",
        "btn_start": "开始转换",

        # 语言胶囊
        "lang_zh": "中文",
        "lang_en": "EN",

        # 横幅 / 日志台
        "banner_done": "已转换",
        "log_empty": "// 暂无日志 — 开始转换后此处实时滚动",
        "log_lines": "{n} 行",
        "log_scrollback": "回滚 -{n} · 滚到底部恢复跟随",

        # 系统日志行
        "sys_scan": "── 预扫描 ──",
        "sys_dry_done": "── 仅分析完成，未生成任何文件 ──",
        "sys_cancel": "── 已取消 ──",
        "sys_start": "══ 转换开始 ══",
        "sys_complete": "══ 转换完成 ══",
        "sys_failed": "══ 发生严重错误 ══",

        # 对话框
        "dlg_dry_title": "仅分析",
        "msg_no_in": "请选择有效的 Fallout 76 输入目录。",
        "msg_no_out": "请选择 Fallout 4 输出目录。",
        "msg_warn_title": "警告",
        "msg_same_dir": "输入目录和输出目录不能相同。\n请重新选择。",
        "dlg_prescan_title": "预扫描结果",
        "dlg_prescan_text": "预扫描完成，确认后开始转换。",
        "dlg_prescan_ask": "是否开始正式转换？",
        "btn_cancel": "取消",
        "dlg_done_title": "转换完成",
        "dlg_done_text": "转换完成！\n\n{summary}\n\n输出目录：\n{out}",
        "btn_open_out": "打开输出文件夹",
        "btn_open_log": "打开日志",
        "btn_again": "重新转换",
        "msg_no_log": "还没有生成日志文件。",
        "msg_fatal": "发生严重错误，详情已写入 error.log：\n",

        # 转换过程日志备注
        "nt_skip_exists": "输出已存在，跳过（未勾选覆盖）",
        "nt_skip_short": "输出已存在，跳过",
        "nt_alpha_bc3": "检测到 Alpha，BC1 → BC3",
        "nt_glow": "从 _l.Alpha 生成 Glow → {fmt}",
        "nt_missing_r": "缺少 _r，未生成 _s（使用 FO4 默认 Specular）",
        "nt_copy_nonmat": "非 BGSM/BGEM 头，原样复制",
        "nt_no_template": "缺少内置模板，原样复制（贴图路径未重写）",
        "nt_tpl_fail": "模板渲染失败: {e}",
        "nt_rebuild": "{kind} 重建（FO4 模板），写入贴图 {n} 条",
        "nt_skip_params": "；忽略的 FO76 专用参数 {n} 项",
        "nt_skip_slot": "忽略 FO76 专用贴图槽: {sp}",
        "nt_copy_suffix": "未识别后缀，原样复制",
        "nt_set_fail": "材质集处理失败: {e}",
        "nt_log_fail": "日志写入失败: {e}",
        "nt_texconv_fallback": "texconv 不可用/失败，{fmt} 降级为 {actual}",

        # 汇总 / 日志文件
        "log_summary": "成功：{total}（转换 {ok} / 生成 {gen} / 复制 {cp}）\n"
                       "警告：{warn}\n"
                       "失败：{err}",
        "log_file_title": "FO76 → FO4 Texture Converter 转换日志",
        "log_file_time": "生成时间",

        # 预扫描报告
        "rep_found": "发现 DDS：{n}",
        "rep_s_exist": "_s（已存在）：{n}",
        "rep_unclassified": "未分类 DDS：{n}",
        "rep_sets": "完整 Material Set：{n}",
        "rep_incomplete": "缺少贴图的 Set：{n}",
        "rep_will_s": "预计生成 _s：{n}",
        "rep_missing_hdr": "—— 缺少贴图的材质（前 50 条）——",
        "rep_missing_item": "  {label}  缺少: {miss}",
        "rep_more": "  …… 另有 {n} 条",
        "rep_uncl_hdr": "—— 未识别后缀的 DDS（将原样复制，前 30 条）——",
        "rep_file_title": "FO76 → FO4 预扫描统计",
        "rep_elapsed": "耗时：{s:.1f} 秒",

        # ------------------------------------------------------------ 提示气泡
        "tip_in": "Fallout 76 的贴图资源目录。\n"
                  "程序会递归扫描其中的 .dds / .bgsm / .bgem 文件。\n"
                  "也可以直接把文件夹拖进窗口。",
        "tip_out": "转换结果的输出目录。\n"
                   "保留目录结构时会在其下复刻输入目录的层级。\n"
                   "注意：不能与输入目录相同。",
        "tip_browse": "打开系统文件夹选择对话框",
        "tip_mode_high": "高质量：BC7 / BC5 · 完整 Mipmap。\n"
                         "适合机场、建筑、车辆等高精度近距离资产。",
        "tip_mode_balanced": "平衡（默认）：BC7 / BC5 · 完整 Mipmap。\n"
                             "画质与体积的折中方案，适合大多数资产。",
        "tip_mode_space": "节省空间：Diffuse=BC1（含 Alpha 自动 BC3），"
                          "Normal 恒为 BC5。\n适合远景与低优先级资产，体积最小。",
        "tip_keep": "开启后在输出目录中复刻输入的文件夹层级；\n"
                    "关闭则所有文件平铺到输出目录根部。",
        "tip_mip": "为贴图生成完整 Mipmap 链。\n"
                   "FO4 引擎需要 Mipmap 才能正确进行远距离贴图过滤，\n"
                   "建议保持开启。",
        "tip_gens": "FO76 的金属度（_l）+ 粗糙度（_r）通道 "
                    "合并生成 FO4 的 Specular（_s）贴图。\n"
                    "缺少任一通道时会按警告跳过。",
        "tip_metal": "FO76 的金属工作流与 FO4 高光工作流不同，\n"
                     "开启后按金属度贴图对 Specular 做补偿提亮。\n"
                     "默认关闭：过度补偿会导致材质发白。",
        "tip_glow_l": "把 _l 贴图的 Alpha 通道提取为 FO4 的 Glow（_g）发光贴图。\n"
                      "仅当资产确实带发光层时开启。",
        "tip_invert": "FO76 与 FO4 的法线贴图 Green（Y）通道方向相反。\n"
                      "光照看起来凹陷 / 凸起反了的时候开启此项翻转。",
        "tip_ao": "把环境光遮蔽（AO）烘焙进 Diffuse 贴图，增强阴影层次。\n"
                  "强度由下方 AO 强度控制。",
        "tip_ao_str": "AO 烘焙强度：0 = 不生效，1 = 完全压暗。\n"
                      "推荐 0.2 – 0.4。",
        "tip_metal_str": "金属补偿强度：0 = 不补偿，1 = 最大提亮。\n"
                         "仅在开启「自动金属补偿」后生效。",
        "tip_fmt_d": "Diffuse（_d 颜色贴图）的压缩格式。\n"
                     "BC7 质量最高，BC3 含 Alpha，BC1 最小。",
        "tip_fmt_n": "Normal（_n 法线贴图）固定为 BC5：\n"
                     "双通道压缩是法线贴图的最优格式，FO4 强制要求。",
        "tip_fmt_s": "Specular（_s 高光贴图）的压缩格式。",
        "tip_fmt_g": "Glow（_g 发光贴图）的压缩格式。",
        "tip_overwrite": "开启后同名输出文件直接覆盖；\n"
                         "关闭则跳过已存在的文件（日志记为 SKIPPED）。",
        "tip_log": "在输出目录生成 conversion_log.txt，\n"
                   "逐文件记录转换结果，便于排查问题。",
        "tip_report": "在输出目录生成 conversion_report.txt，\n"
                      "汇总数量、体积变化与警告统计。",
        "tip_bgsm": "同时转换材质文件 .bgsm / .bgem（实验性）。\n"
                    "基于内置模板写入贴图路径，复杂材质请人工复核。",
        "tip_dry": "只扫描并分析输入目录，不写任何文件。\n"
                   "用于转换前确认将处理哪些文件。",
        "tip_start": "开始批量转换：扫描 → 分析 → 转换 → 生成 → 写入。\n"
                     "过程中日志台实时滚动，可随时查看进度。",
        "tip_lang": "切换界面语言",
    },

    # ------------------------------------------------------------------ English
    "en": {
        "window_title": "FO76 → FO4 Texture Converter",

        "tab_basic": "BASIC",
        "tab_options": "OPTIONS",
        "tab_tex": "TEXTURES",
        "tab_mat": "MATERIAL",
        "tab_out": "OUTPUT",

        "gb_dirs": "DIRECTORIES",
        "gb_mode": "CONVERT MODE",
        "gb_opts": "QUICK OPTIONS",

        "lbl_in": "FO76 INPUT",
        "lbl_out": "FO4 OUTPUT",
        "ph_in": "Drop a Fallout 76 asset folder here, or click Browse",
        "ph_out": "Choose the Fallout 4 output folder (must differ from input)",
        "btn_browse": "BROWSE",
        "pick_in": "Choose the Fallout 76 input folder",
        "pick_out": "Choose the Fallout 4 output folder",

        "mode_high": "HIGH",
        "mode_balanced": "BALANCED",
        "mode_space": "SPACE-SAVER",

        "cb_keep": "Keep directory structure",
        "cb_mip": "Generate Mipmaps",
        "cb_gens": "Generate FO4 _s.dds (_l + _r → _s)",
        "cb_metal": "Metal compensation (off by default)",
        "cb_glow_l": "Glow from _l Alpha (off by default)",

        "lbl_diffuse": "Diffuse Format",
        "lbl_normal": "Normal Format (always BC5)",
        "lbl_spec": "Specular Format",
        "lbl_glow": "Glow Format",
        "note_tex": "Changing the convert mode on the BASIC tab resets "
                    "the format presets here.",

        "cb_invert": "Invert normal Green channel (off by default)",
        "cb_ao": "Bake AO → Diffuse (off by default)",
        "lbl_ao": "AO Strength",
        "lbl_metal": "Metal Strength",
        "note_mat": "SSS (_l.B) is ignored by default: vanilla FO4 BGSM has "
                    "no matching parameter. Nothing is written to _s and "
                    "Diffuse stays untouched.",

        "cb_overwrite": "Overwrite existing output files",
        "cb_log": "Write conversion_log.txt",
        "cb_report": "Write conversion_report.txt",
        "cb_bgsm": "Convert BGSM / BGEM (experimental)",

        "btn_dry": "ANALYZE ONLY",
        "btn_start": "START",

        "lang_zh": "中文",
        "lang_en": "EN",

        # Banner / log console
        "banner_done": "CONVERTED",
        "log_empty": "// NO LOGS YET — LIVE OUTPUT STREAMS HERE",
        "log_lines": "{n} LINES",
        "log_scrollback": "SCROLLBACK -{n} · SCROLL TO BOTTOM TO FOLLOW",

        # System log lines
        "sys_scan": "── PRE-SCAN ──",
        "sys_dry_done": "── ANALYSIS DONE, NOTHING WRITTEN ──",
        "sys_cancel": "── CANCELLED ──",
        "sys_start": "══ CONVERSION STARTED ══",
        "sys_complete": "══ CONVERSION COMPLETE ══",
        "sys_failed": "══ FATAL ERROR ══",

        # Dialogs
        "dlg_dry_title": "ANALYZE ONLY",
        "msg_no_in": "Please choose a valid Fallout 76 input folder.",
        "msg_no_out": "Please choose the Fallout 4 output folder.",
        "msg_warn_title": "WARNING",
        "msg_same_dir": "Input and output folders must be different.\n"
                        "Please choose again.",
        "dlg_prescan_title": "PRE-SCAN RESULT",
        "dlg_prescan_text": "Pre-scan complete. Confirm to start conversion.",
        "dlg_prescan_ask": "Start the full conversion now?",
        "btn_cancel": "CANCEL",
        "dlg_done_title": "CONVERSION COMPLETE",
        "dlg_done_text": "Conversion complete!\n\n{summary}\n\n"
                         "Output folder:\n{out}",
        "btn_open_out": "OPEN OUTPUT FOLDER",
        "btn_open_log": "OPEN LOG",
        "btn_again": "CONVERT AGAIN",
        "msg_no_log": "No log file has been generated yet.",
        "msg_fatal": "A fatal error occurred; details were written to "
                     "error.log:\n",

        # Conversion log notes
        "nt_skip_exists": "Output exists, skipped (overwrite is off)",
        "nt_skip_short": "Output exists, skipped",
        "nt_alpha_bc3": "Alpha detected, BC1 → BC3",
        "nt_glow": "Glow built from _l.Alpha → {fmt}",
        "nt_missing_r": "Missing _r — _s not generated "
                        "(FO4 default Specular)",
        "nt_copy_nonmat": "Not a BGSM/BGEM header, copied as-is",
        "nt_no_template": "Built-in template missing, copied as-is "
                          "(paths not rewritten)",
        "nt_tpl_fail": "Template render failed: {e}",
        "nt_rebuild": "{kind} rebuilt (FO4 template), {n} texture paths written",
        "nt_skip_params": "; ignored {n} FO76-only params",
        "nt_skip_slot": "Ignored FO76-only texture slot: {sp}",
        "nt_copy_suffix": "Unrecognized suffix, copied as-is",
        "nt_set_fail": "Material set failed: {e}",
        "nt_log_fail": "Log write failed: {e}",
        "nt_texconv_fallback": "texconv unavailable/failed, "
                               "{fmt} fell back to {actual}",

        # Summary / log file
        "log_summary": "SUCCESS: {total} (converted {ok} / generated {gen} / "
                       "copied {cp})\nWARNINGS: {warn}\nFAILED: {err}",
        "log_file_title": "FO76 → FO4 Texture Converter Conversion Log",
        "log_file_time": "Generated at",

        # Pre-scan report
        "rep_found": "DDS found: {n}",
        "rep_s_exist": "_s (already present): {n}",
        "rep_unclassified": "Unclassified DDS: {n}",
        "rep_sets": "Complete material sets: {n}",
        "rep_incomplete": "Incomplete sets: {n}",
        "rep_will_s": "_s to generate: {n}",
        "rep_missing_hdr": "—— MATERIALS WITH MISSING MAPS (first 50) ——",
        "rep_missing_item": "  {label}  missing: {miss}",
        "rep_more": "  …… and {n} more",
        "rep_uncl_hdr": "—— DDS WITH UNRECOGNIZED SUFFIX "
                        "(copied as-is, first 30) ——",
        "rep_file_title": "FO76 → FO4 PRE-SCAN STATISTICS",
        "rep_elapsed": "Elapsed: {s:.1f} s",

        "tip_in": "The Fallout 76 texture asset folder.\n"
                  "It is scanned recursively for .dds / .bgsm / .bgem files.\n"
                  "You can also drag a folder onto the window.",
        "tip_out": "Destination folder for converted files.\n"
                   "With structure kept, the input hierarchy is mirrored.\n"
                   "Must not be the same as the input folder.",
        "tip_browse": "Open the system folder picker",
        "tip_mode_high": "HIGH: BC7 / BC5, full mipmaps.\n"
                         "Best for close-up hero assets like architecture "
                         "and vehicles.",
        "tip_mode_balanced": "BALANCED (default): BC7 / BC5, full mipmaps.\n"
                             "A middle ground between quality and size.",
        "tip_mode_space": "SPACE-SAVER: Diffuse=BC1 (BC3 when alpha exists), "
                          "Normal always BC5.\nSmallest output, for distant or "
                          "low-priority assets.",
        "tip_keep": "ON: mirror the input folder hierarchy in the output.\n"
                    "OFF: flatten every file into the output root.",
        "tip_mip": "Generate full mipmap chains.\n"
                   "FO4 needs mipmaps for correct distance filtering.\n"
                   "Recommended to keep ON.",
        "tip_gens": "Merge FO76 metalness (_l) + roughness (_r) channels\n"
                    "into the FO4 Specular (_s) map.\n"
                    "Missing channels are skipped with a warning.",
        "tip_metal": "FO76 uses a metalness workflow while FO4 is specular.\n"
                     "ON: brighten Specular based on the metalness map.\n"
                     "Default OFF: over-compensation washes materials out.",
        "tip_glow_l": "Extract the _l map's alpha channel as the FO4\n"
                      "Glow (_g) emissive map. Enable only when the asset\n"
                      "actually has an emissive layer.",
        "tip_invert": "FO76 and FO4 store the normal map Green (Y) channel\n"
                      "in opposite directions. Enable this if lighting looks\n"
                      "inverted (dents look like bumps).",
        "tip_ao": "Bake ambient occlusion into the Diffuse map for\n"
                  "deeper shading. Controlled by AO Strength below.",
        "tip_ao_str": "AO bake strength: 0 = no effect, 1 = fully darkened.\n"
                      "Recommended 0.2 – 0.4.",
        "tip_metal_str": "Metal compensation strength: 0 = none, 1 = max.\n"
                         "Only applies when Metal compensation is ON.",
        "tip_fmt_d": "Compression format for Diffuse (_d) maps.\n"
                     "BC7 = best quality, BC3 = with alpha, BC1 = smallest.",
        "tip_fmt_n": "Normal (_n) maps are locked to BC5:\n"
                     "two-channel compression is optimal for normals\n"
                     "and required by FO4.",
        "tip_fmt_s": "Compression format for Specular (_s) maps.",
        "tip_fmt_g": "Compression format for Glow (_g) maps.",
        "tip_overwrite": "ON: overwrite same-name output files.\n"
                         "OFF: skip them (logged as SKIPPED).",
        "tip_log": "Write conversion_log.txt into the output folder,\n"
                   "recording the per-file result for troubleshooting.",
        "tip_report": "Write conversion_report.txt into the output folder,\n"
                      "summarizing counts, size delta and warnings.",
        "tip_bgsm": "Also convert .bgsm / .bgem material files (experimental).\n"
                    "Texture paths are written from built-in templates;\n"
                    "double-check complex materials by hand.",
        "tip_dry": "Scan and analyze the input folder without writing\n"
                   "anything. Use it to preview what would be processed.",
        "tip_start": "Start the batch: SCAN → ANALYZE → CONVERT →\n"
                     "GENERATE → WRITE. Watch the live log for progress.",
        "tip_lang": "Switch language",
    },
}


def tr(key: str, **kwargs) -> str:
    """按当前界面语言取文案，可选 .format 参数。"""
    text = S.get(LANG, S["zh"]).get(key, S["zh"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def set_lang(lang: str):
    global LANG
    if lang in S:
        LANG = lang
