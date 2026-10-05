"""
Generate Google Slides presentation for LogSentinel (.pptx format natively importable by Google Slides).
Replicates the visual layout, card design, 3-column structures, milestone steps, and color aesthetics
from the PlayMap and FlutterForecast presentation templates.
"""

import os
import json
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

def create_google_slides_deck():
    prs = Presentation()
    # 16:9 widescreen layout
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6] # completely blank slide

    # Color Palette: Modern Cybernetic Observability
    COLOR_BG = RGBColor(11, 17, 32)         # #0B1120 Dark Navy
    COLOR_CARD = RGBColor(22, 31, 48)       # #161F30 Card Surface
    COLOR_CARD_BORDER = RGBColor(59, 130, 246) # #3B82F6 Border
    COLOR_EMERALD = RGBColor(16, 185, 129)  # #10B981 Neon Emerald
    COLOR_CYAN = RGBColor(6, 182, 212)      # #06B6D4 Electric Cyan
    COLOR_BLUE = RGBColor(59, 130, 246)     # #3B82F6 Royal Blue
    COLOR_ORANGE = RGBColor(249, 115, 22)   # #F97316 Alert Orange
    COLOR_PURPLE = RGBColor(139, 92, 246)   # #8B5CF6 Deep Violet
    COLOR_WHITE = RGBColor(255, 255, 255)   # White
    COLOR_SLATE_LIGHT = RGBColor(226, 232, 240) # #E2E8F0
    COLOR_SLATE_MUTED = RGBColor(148, 163, 184) # #94A3B8
    COLOR_GOLD = RGBColor(245, 158, 11)     # #F59E0B Amber Gold

    def apply_background(slide):
        bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
        bg.fill.solid()
        bg.fill.fore_color.rgb = COLOR_BG
        bg.line.fill.background() # no line
        return bg

    def add_header(slide, category, title, headline, template_ref):
        # Section Category Badge
        cat_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(8.0), Inches(0.4))
        tf_cat = cat_box.text_frame
        tf_cat.word_wrap = True
        p_cat = tf_cat.paragraphs[0]
        p_cat.text = category.upper()
        p_cat.font.name = "Consolas"
        p_cat.font.size = Pt(11)
        p_cat.font.bold = True
        p_cat.font.color.rgb = COLOR_EMERALD

        # Slide Watermark / Template Ref
        ref_box = slide.shapes.add_textbox(Inches(8.5), Inches(0.4), Inches(4.0), Inches(0.4))
        tf_ref = ref_box.text_frame
        p_ref = tf_ref.paragraphs[0]
        p_ref.text = template_ref
        p_ref.alignment = PP_ALIGN.RIGHT
        p_ref.font.name = "Consolas"
        p_ref.font.size = Pt(10)
        p_ref.font.color.rgb = COLOR_SLATE_MUTED

        # Main Title
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.75), Inches(11.7), Inches(0.6))
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        p_title = tf_title.paragraphs[0]
        p_title.text = title
        p_title.font.name = "Trebuchet MS"
        p_title.font.size = Pt(24)
        p_title.font.bold = True
        p_title.font.color.rgb = COLOR_WHITE

        # Headline / Subtitle
        sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.35), Inches(11.7), Inches(0.4))
        tf_sub = sub_box.text_frame
        tf_sub.word_wrap = True
        p_sub = tf_sub.paragraphs[0]
        p_sub.text = headline
        p_sub.font.name = "Trebuchet MS"
        p_sub.font.size = Pt(13)
        p_sub.font.italic = True
        p_sub.font.color.rgb = COLOR_CYAN

    def add_bottom_metric_bar(slide, metric_text):
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(6.6), Inches(11.733), Inches(0.5))
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(15, 23, 42)
        card.line.color.rgb = COLOR_EMERALD
        card.line.width = Pt(1)

        tb = slide.shapes.add_textbox(Inches(1.0), Inches(6.62), Inches(11.3), Inches(0.45))
        tf = tb.text_frame
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.text = f"⚡ KEY METRICS & BENCHMARKS:  {metric_text}"
        p.font.name = "Consolas"
        p.font.size = Pt(10)
        p.font.bold = True
        p.font.color.rgb = COLOR_EMERALD

    def add_speaker_notes(slide, notes_text):
        notes_slide = slide.notes_slide
        tf_notes = notes_slide.notes_text_frame
        tf_notes.text = notes_text

    # =========================================================================
    # SLIDE 1: Title & Vision (Cover)
    # =========================================================================
    s1 = prs.slides.add_slide(blank_layout)
    apply_background(s1)

    # Glowing emblem container
    emblem = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(5.666), Inches(0.9), Inches(2.0), Inches(2.0))
    emblem.fill.solid()
    emblem.fill.fore_color.rgb = RGBColor(16, 185, 129)
    emblem.line.color.rgb = COLOR_CYAN
    emblem.line.width = Pt(2)

    emblem_tb = s1.shapes.add_textbox(Inches(5.666), Inches(1.1), Inches(2.0), Inches(1.5))
    tf_e = emblem_tb.text_frame
    p_e = tf_e.paragraphs[0]
    p_e.text = "LS"
    p_e.alignment = PP_ALIGN.CENTER
    p_e.font.name = "Trebuchet MS"
    p_e.font.size = Pt(64)
    p_e.font.bold = True
    p_e.font.color.rgb = RGBColor(7, 11, 20)

    # Title
    t_box = s1.shapes.add_textbox(Inches(1.0), Inches(3.1), Inches(11.333), Inches(0.9))
    tf_t = t_box.text_frame
    p_t = tf_t.paragraphs[0]
    p_t.text = "LogSentinel"
    p_t.alignment = PP_ALIGN.CENTER
    p_t.font.name = "Trebuchet MS"
    p_t.font.size = Pt(48)
    p_t.font.bold = True
    p_t.font.color.rgb = COLOR_WHITE

    # Subtitle
    sub_box = s1.shapes.add_textbox(Inches(1.5), Inches(4.0), Inches(10.333), Inches(0.8))
    tf_s = sub_box.text_frame
    tf_s.word_wrap = True
    p_s = tf_s.paragraphs[0]
    p_s.text = "Real-Time Unsupervised Log Anomaly Detection, Dynamic Topology Mapping\n& Root-Cause Blast-Radius Ranking"
    p_s.alignment = PP_ALIGN.CENTER
    p_s.font.name = "Calibri"
    p_s.font.size = Pt(17)
    p_s.font.color.rgb = COLOR_SLATE_LIGHT

    # Slogan pill (PlayMap style "your journey starts here!")
    pill = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(3.9), Inches(5.1), Inches(5.5), Inches(0.65))
    pill.fill.solid()
    pill.fill.fore_color.rgb = COLOR_ORANGE
    pill.line.fill.background()

    pill_tb = s1.shapes.add_textbox(Inches(3.9), Inches(5.15), Inches(5.5), Inches(0.5))
    tf_p = pill_tb.text_frame
    p_p = tf_p.paragraphs[0]
    p_p.text = "Taming Microservice Chaos in Real Time"
    p_p.alignment = PP_ALIGN.CENTER
    p_p.font.name = "Trebuchet MS"
    p_p.font.size = Pt(14)
    p_p.font.bold = True
    p_p.font.color.rgb = COLOR_WHITE

    # Footer metadata
    f_box = s1.shapes.add_textbox(Inches(1.0), Inches(6.4), Inches(11.333), Inches(0.5))
    tf_f = f_box.text_frame
    p_f = tf_f.paragraphs[0]
    p_f.text = "Open Source (Apache 2.0)  •  Python 3.11 / FastAPI  •  TimescaleDB  •  Valkey 8.0  •  React 18"
    p_f.alignment = PP_ALIGN.CENTER
    p_f.font.name = "Consolas"
    p_f.font.size = Pt(11)
    p_f.font.color.rgb = COLOR_SLATE_MUTED

    add_speaker_notes(s1, (
        "Welcome everyone. Today we are presenting LogSentinel—an intelligent, high-throughput observability platform "
        "engineered to solve one of distributed computing's toughest challenges: microservice chaos. "
        "LogSentinel ingests millions of logs in real time, extracts semantic templates without human intervention, "
        "catches unseen anomalies with unsupervised machine learning, and maps the exact cascading blast radius "
        "so engineering teams pinpoint true root causes in seconds."
    ))

    # =========================================================================
    # SLIDE 2: The Engineering Team (PlayMap & FlutterForecast Style)
    # =========================================================================
    s2 = prs.slides.add_slide(blank_layout)
    apply_background(s2)
    add_header(s2, "Team", "The Engineering Team", "Core Architecture, Distributed Streaming, ML Systems & QA", "Template: PlayMap #2 / FlutterForecast #2")

    members = [
        ("Melos Hajrullahu", "Team Leader", "ML Systems & Anomaly Modeling", COLOR_EMERALD),
        ("Leorent Ismajli", "Core Engine Lead", "Valkey Streams & Drain3 Mining", COLOR_CYAN),
        ("Blerim Haxhiu", "Frontend Specialist", "React 18 & Cytoscape Topology", COLOR_BLUE),
        ("Blert Sylejmani", "Database Architect", "FastAPI & TimescaleDB Engine", COLOR_PURPLE),
        ("Juled Morina", "SRE & Chaos Lead", "Chaos Injection & Hardening", COLOR_ORANGE)
    ]

    card_width = Inches(2.15)
    gap = Inches(0.24)
    start_x = Inches(0.8)

    for i, (name, role, desc, accent) in enumerate(members):
        x = start_x + i * (card_width + gap)
        # Avatar Circle
        circle = s2.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.35), Inches(2.0), Inches(1.45), Inches(1.45))
        circle.fill.solid()
        circle.fill.fore_color.rgb = RGBColor(26, 38, 59)
        circle.line.color.rgb = accent
        circle.line.width = Pt(2.5)

        # Monogram in circle
        c_tb = s2.shapes.add_textbox(x + Inches(0.35), Inches(2.35), Inches(1.45), Inches(0.7))
        c_p = c_tb.text_frame.paragraphs[0]
        c_p.text = "".join([part[0] for part in name.split()])
        c_p.alignment = PP_ALIGN.CENTER
        c_p.font.name = "Trebuchet MS"
        c_p.font.size = Pt(22)
        c_p.font.bold = True
        c_p.font.color.rgb = accent

        # Card body
        body_card = s2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(3.7), card_width, Inches(2.6))
        body_card.fill.solid()
        body_card.fill.fore_color.rgb = COLOR_CARD
        body_card.line.color.rgb = RGBColor(30, 41, 59)
        body_card.line.width = Pt(1)

        b_tb = s2.shapes.add_textbox(x + Inches(0.1), Inches(3.8), card_width - Inches(0.2), Inches(2.4))
        tf_b = b_tb.text_frame
        tf_b.word_wrap = True

        p_name = tf_b.paragraphs[0]
        p_name.text = name
        p_name.alignment = PP_ALIGN.CENTER
        p_name.font.name = "Trebuchet MS"
        p_name.font.size = Pt(13)
        p_name.font.bold = True
        p_name.font.color.rgb = COLOR_WHITE

        p_role = tf_b.add_paragraph()
        p_role.text = role
        p_role.alignment = PP_ALIGN.CENTER
        p_role.font.name = "Calibri"
        p_role.font.size = Pt(10)
        p_role.font.bold = True
        p_role.font.color.rgb = accent

        p_desc = tf_b.add_paragraph()
        p_desc.text = f"\n{desc}"
        p_desc.alignment = PP_ALIGN.CENTER
        p_desc.font.name = "Calibri"
        p_desc.font.size = Pt(9.5)
        p_desc.font.color.rgb = COLOR_SLATE_MUTED

    add_bottom_metric_bar(s2, "5 Core Engineers | 100% Passing Tests | Multi-Tenant Architecture Validated")
    add_speaker_notes(s2, (
        "Our team brings together deep expertise in distributed systems, streaming data, and machine learning: "
        "Melos led our ML modeling and threshold sweep; Leorent built our Valkey streaming buffer and Drain3 integration; "
        "Blerim engineered our interactive React 18 topology graph; Blert optimized the TimescaleDB hypertables; "
        "and Juled designed our chaos testing and capacity benchmarks."
    ))

    # =========================================================================
    # SLIDE 3: The Problem (PlayMap 3-Card Style)
    # =========================================================================
    s3 = prs.slides.add_slide(blank_layout)
    apply_background(s3)
    add_header(s3, "Problem", "The Problem: Microservice Chaos & Blindspots", "Why Traditional Observability Breaks in Modern Cloud Environments", "Template: PlayMap #3 (3 Numbered Cards)")

    problems = [
        ("1", "Cascading Failures", "Decoupled Complexity", (
            "• Modern clouds run dozens of decoupled microservices.\n\n"
            "• When a single database or auth node experiences latency, errors cascade violently down the chain.\n\n"
            "• Millions of confusing, unstructured log lines flood in seconds, burying the original trigger."
        ), COLOR_ORANGE),
        ("2", "Alert Fatigue", "Low-Retention & Noise", (
            "• SREs are bombarded by hundreds of simultaneous alerts.\n\n"
            "• Over 70% of alerts during an incident are downstream symptoms (e.g. 504 Gateway Timeouts, retries).\n\n"
            "• Critical root-cause signals are drowned out, causing panic and triage paralysis."
        ), COLOR_PURPLE),
        ("3", "Content Overload", "Skyrocketing MTTR Cost", (
            "• Manual log grep, indexing searches, and timeline reconstruction take 45 to 90 minutes on average.\n\n"
            "• Enterprise downtime costs exceed $5,600+ per minute ($300,000+ per hour).\n\n"
            "• Protracted outages directly cause customer churn and severe SLA breach penalties."
        ), COLOR_CYAN)
    ]

    c_width = Inches(3.7)
    c_gap = Inches(0.3)
    c_start_x = Inches(0.8)

    for i, (num, title, subtitle, content, accent) in enumerate(problems):
        x = c_start_x + i * (c_width + c_gap)
        card = s3.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), c_width, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        # Number circle badge
        badge = s3.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.3), Inches(2.2), Inches(0.6), Inches(0.6))
        badge.fill.solid()
        badge.fill.fore_color.rgb = accent
        badge.line.fill.background()
        b_tb = s3.shapes.add_textbox(x + Inches(0.3), Inches(2.25), Inches(0.6), Inches(0.5))
        b_tb.text_frame.paragraphs[0].text = num
        b_tb.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        b_tb.text_frame.paragraphs[0].font.name = "Trebuchet MS"
        b_tb.text_frame.paragraphs[0].font.size = Pt(14)
        b_tb.text_frame.paragraphs[0].font.bold = True
        b_tb.text_frame.paragraphs[0].font.color.rgb = COLOR_WHITE

        # Text box
        tb = s3.shapes.add_textbox(x + Inches(0.3), Inches(3.0), c_width - Inches(0.6), Inches(3.1))
        tf = tb.text_frame
        tf.word_wrap = True

        p1 = tf.paragraphs[0]
        p1.text = title
        p1.font.name = "Trebuchet MS"
        p1.font.size = Pt(16)
        p1.font.bold = True
        p1.font.color.rgb = COLOR_WHITE

        p2 = tf.add_paragraph()
        p2.text = subtitle
        p2.font.name = "Calibri"
        p2.font.size = Pt(11)
        p2.font.italic = True
        p2.font.color.rgb = accent

        p3 = tf.add_paragraph()
        p3.text = f"\n{content}"
        p3.font.name = "Calibri"
        p3.font.size = Pt(10)
        p3.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s3, "$5,600/min Average Downtime Cost | 70%+ Symptom Noise | 45-90 min Average MTTR")
    add_speaker_notes(s3, (
        "Here is the core problem: Microservice architectures promise agility, but create operational chaos. "
        "When an auth service degrades, 20 downstream services fire alerts simultaneously. SREs are overwhelmed "
        "by noise, and finding the culprit takes an hour. At $5,600 per downtime minute, companies lose millions."
    ))

    # =========================================================================
    # SLIDE 4: Data-Driven Research & Benchmarks (PlayMap #4 & FlutterForecast #3)
    # =========================================================================
    s4 = prs.slides.add_slide(blank_layout)
    apply_background(s4)
    add_header(s4, "Research", "Data-Driven Research & Empirical Validation", "Operational Statistics from Industry Studies & LogSentinel Validation", "Template: PlayMap #4 / FlutterForecast #3")

    stats = [
        ("82%", "Cascading Outages", "of modern enterprise cloud outages originate from interconnected microservice cascades.", COLOR_ORANGE),
        ("67%", "MTTR Wasted", "of total incident triage time is spent merely finding which service failed first.", COLOR_PURPLE),
        ("10k+", "Logs / Second", "sustained ingestion throughput achieved per worker node with zero backpressure.", COLOR_EMERALD),
        ("0.959", "ROC-AUC Score", "discriminatory accuracy achieved by LogSentinel's tuned Isolation Forest.", COLOR_CYAN)
    ]

    stat_w = Inches(2.7)
    stat_gap = Inches(0.31)
    stat_x = Inches(0.8)

    for i, (val, title, desc, accent) in enumerate(stats):
        x = stat_x + i * (stat_w + stat_gap)
        card = s4.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), stat_w, Inches(2.1))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s4.shapes.add_textbox(x + Inches(0.15), Inches(2.1), stat_w - Inches(0.3), Inches(1.8))
        tf = tb.text_frame
        tf.word_wrap = True

        p_val = tf.paragraphs[0]
        p_val.text = val
        p_val.alignment = PP_ALIGN.CENTER
        p_val.font.name = "Trebuchet MS"
        p_val.font.size = Pt(36)
        p_val.font.bold = True
        p_val.font.color.rgb = accent

        p_title = tf.add_paragraph()
        p_title.text = title
        p_title.alignment = PP_ALIGN.CENTER
        p_title.font.name = "Calibri"
        p_title.font.size = Pt(12)
        p_title.font.bold = True
        p_title.font.color.rgb = COLOR_WHITE

        p_desc = tf.add_paragraph()
        p_desc.text = desc
        p_desc.alignment = PP_ALIGN.CENTER
        p_desc.font.name = "Calibri"
        p_desc.font.size = Pt(9.5)
        p_desc.font.color.rgb = COLOR_SLATE_MUTED

    # Bottom summary comparison box
    box = s4.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(4.35), Inches(11.733), Inches(2.0))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(15, 23, 42)
    box.line.color.rgb = COLOR_BLUE
    box.line.width = Pt(1)

    box_tb = s4.shapes.add_textbox(Inches(1.1), Inches(4.45), Inches(11.1), Inches(1.8))
    tf_box = box_tb.text_frame
    tf_box.word_wrap = True

    p_b1 = tf_box.paragraphs[0]
    p_b1.text = "EMPIRICAL BENCHMARK ANALYSIS (backend/benchmark_results.json & ml_evaluation_results.json):"
    p_b1.font.name = "Consolas"
    p_b1.font.size = Pt(11)
    p_b1.font.bold = True
    p_b1.font.color.rgb = COLOR_CYAN

    p_b2 = tf_box.add_paragraph()
    p_b2.text = (
        "• High Compression Ratio: Drain3 online template miner achieves 95%+ data reduction by clustering raw logs into semantic templates.\n"
        "• Low Latency Overhead: HTTP p50 latency is ~116ms; end-to-end telemetry broadcast over WebSockets completes in <120ms.\n"
        "• Optimal Decision Boundary: Exhaustive threshold sweep identified -0.075 as optimal, yielding 81.25% precision and 86.67% recall.\n"
        "• Resource Efficiency: Average memory utilization stays under 52MB RSS, running stably under high concurrency (50 parallel workers)."
    )
    p_b2.font.name = "Calibri"
    p_b2.font.size = Pt(10)
    p_b2.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s4, "95%+ Log Compression | 0.959 ROC-AUC | 81.25% Precision | <52MB RAM Footprint")
    add_speaker_notes(s4, (
        "Our approach is backed by empirical data: Studies confirm that 82% of outages are cascades and 67% of triage "
        "is spent locating the origin. On real failure traces—SQL injection, thread starvation, latency spikes—"
        "LogSentinel achieves a 0.959 ROC-AUC and compresses logs by 95% while keeping memory under 52 megabytes."
    ))

    # =========================================================================
    # SLIDE 5: The LogSentinel Solution (PlayMap #5 3-Pillar Style)
    # =========================================================================
    s5 = prs.slides.add_slide(blank_layout)
    apply_background(s5)
    add_header(s5, "Solution", "The LogSentinel Solution: Autonomous Triad", "Stream Processing + Unsupervised ML + Causal Graph Theory", "Template: PlayMap #5 (3 Numbered Solution Cards)")

    solutions = [
        ("1", "Stream Ingestion & Mining", "Valkey Buffer & Drain3", (
            "• Universal ingestion via OpenTelemetry (OTLP), Fluent Bit, Vector, and Python SDK.\n\n"
            "• Non-blocking Valkey memory stream buffer (XADD) prevents ingestion bottlenecks.\n\n"
            "• Drain3 tree-based online miner compresses logs by 95% into structured templates in <0.5ms."
        ), COLOR_EMERALD),
        ("2", "Unsupervised Detection", "Sliding-Window iForest", (
            "• 60s sliding windows with 30s stride extract 12 statistical, entropy, and burst features.\n\n"
            "• Unsupervised Isolation Forest detects zero-day anomalies without training labels.\n\n"
            "• Eliminates brittle regex rules, threshold tuning, and manual alert configuration."
        ), COLOR_BLUE),
        ("3", "Causal Topology & Blast", "NetworkX Directed Graph", (
            "• Reconstructs service dependency topology dynamically from live runtime traffic.\n\n"
            "• Ranks the upstream root cause and measures downstream blast radius.\n\n"
            "• Broadcasts incident alerts via WebSockets to React dashboard in under 120ms."
        ), COLOR_PURPLE)
    ]

    for i, (num, title, subtitle, content, accent) in enumerate(solutions):
        x = c_start_x + i * (c_width + c_gap)
        card = s5.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), c_width, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        badge = s5.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.3), Inches(2.2), Inches(0.6), Inches(0.6))
        badge.fill.solid()
        badge.fill.fore_color.rgb = accent
        badge.line.fill.background()
        b_tb = s5.shapes.add_textbox(x + Inches(0.3), Inches(2.25), Inches(0.6), Inches(0.5))
        b_tb.text_frame.paragraphs[0].text = num
        b_tb.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        b_tb.text_frame.paragraphs[0].font.name = "Trebuchet MS"
        b_tb.text_frame.paragraphs[0].font.size = Pt(14)
        b_tb.text_frame.paragraphs[0].font.bold = True
        b_tb.text_frame.paragraphs[0].font.color.rgb = COLOR_WHITE

        tb = s5.shapes.add_textbox(x + Inches(0.3), Inches(3.0), c_width - Inches(0.6), Inches(3.1))
        tf = tb.text_frame
        tf.word_wrap = True

        p1 = tf.paragraphs[0]
        p1.text = title
        p1.font.name = "Trebuchet MS"
        p1.font.size = Pt(16)
        p1.font.bold = True
        p1.font.color.rgb = COLOR_WHITE

        p2 = tf.add_paragraph()
        p2.text = subtitle
        p2.font.name = "Calibri"
        p2.font.size = Pt(11)
        p2.font.italic = True
        p2.font.color.rgb = accent

        p3 = tf.add_paragraph()
        p3.text = f"\n{content}"
        p3.font.name = "Calibri"
        p3.font.size = Pt(10)
        p3.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s5, "Zero Manual Rules | O(1) Memory Ingestion Buffer | Sub-120ms End-to-End Latency")
    add_speaker_notes(s5, (
        "LogSentinel solves this with 3 pillars: Pillar 1: High-throughput ingestion buffering and Drain3 template "
        "compression. Pillar 2: Unsupervised Isolation Forests running over sliding windows, catching zero-day anomalies "
        "without human-labeled datasets. Pillar 3: A dynamic causal graph that isolates the true root cause and calculates "
        "the blast radius in real time."
    ))

    # =========================================================================
    # SLIDE 6: Solution Lifecycle & Phases (FlutterForecast 5-Phase Circle Style)
    # =========================================================================
    s6 = prs.slides.add_slide(blank_layout)
    apply_background(s6)
    add_header(s6, "Phases", "Engineering Lifecycle & Solution Phases", "From Algorithmic Theory to Production Hardening", "Template: FlutterForecast #6 (5 Connected Milestone Steps)")

    phases = [
        ("01", "Intensive\nResearch", "Drain3 depth & Isolation Forest hyperparameter tuning.", COLOR_EMERALD),
        ("02", "Data Ingestion &\nPreprocessing", "OTLP, Fluent Bit, Vector sinks & token sanitization.", COLOR_CYAN),
        ("03", "ML & Causal\nEngine", "12-feature sliding vectors & NetworkX causal graph.", COLOR_BLUE),
        ("04", "Full-Stack\nDevelopment", "FastAPI async backend & React 18 / Cytoscape dashboard.", COLOR_PURPLE),
        ("05", "Chaos Testing &\nHardening", "Synthetic chaos injection, benchmarks & Docker fleet.", COLOR_ORANGE)
    ]

    p_w = Inches(2.15)
    p_gap = Inches(0.24)
    p_x = Inches(0.8)

    # Connector line behind circles
    line = s6.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1.5), Inches(2.7), Inches(10.3), Inches(0.08))
    line.fill.solid()
    line.fill.fore_color.rgb = RGBColor(30, 41, 59)
    line.line.fill.background()

    for i, (step, name, desc, accent) in enumerate(phases):
        x = p_x + i * (p_w + p_gap)

        # Milestone Circle
        circle = s6.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.35), Inches(2.0), Inches(1.45), Inches(1.45))
        circle.fill.solid()
        circle.fill.fore_color.rgb = RGBColor(22, 31, 48)
        circle.line.color.rgb = accent
        circle.line.width = Pt(2.5)

        c_tb = s6.shapes.add_textbox(x + Inches(0.35), Inches(2.4), Inches(1.45), Inches(0.7))
        c_p = c_tb.text_frame.paragraphs[0]
        c_p.text = step
        c_p.alignment = PP_ALIGN.CENTER
        c_p.font.name = "Consolas"
        c_p.font.size = Pt(22)
        c_p.font.bold = True
        c_p.font.color.rgb = accent

        # Content Card
        card = s6.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(3.7), p_w, Inches(2.6))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = RGBColor(30, 41, 59)
        card.line.width = Pt(1)

        tb = s6.shapes.add_textbox(x + Inches(0.1), Inches(3.8), p_w - Inches(0.2), Inches(2.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p_name = tf.paragraphs[0]
        p_name.text = name
        p_name.alignment = PP_ALIGN.CENTER
        p_name.font.name = "Trebuchet MS"
        p_name.font.size = Pt(12)
        p_name.font.bold = True
        p_name.font.color.rgb = COLOR_WHITE

        p_desc = tf.add_paragraph()
        p_desc.text = f"\n{desc}"
        p_desc.alignment = PP_ALIGN.CENTER
        p_desc.font.name = "Calibri"
        p_desc.font.size = Pt(9.5)
        p_desc.font.color.rgb = COLOR_SLATE_MUTED

    add_bottom_metric_bar(s6, "5 Phased Milestones | 100% Turnkey Deployment via Docker Compose")
    add_speaker_notes(s6, (
        "Following FlutterForecast's phase structure, LogSentinel was delivered across 5 rigorous engineering milestones: "
        "Algorithmic research, universal protocol ingestion, ML and graph engine construction, full-stack reactive development, "
        "and chaos testing under simulated production failures."
    ))

    # =========================================================================
    # SLIDE 7: Technologies: Front-End (PlayMap #6 3-Column Color Block Style)
    # =========================================================================
    s7 = prs.slides.add_slide(blank_layout)
    apply_background(s7)
    add_header(s7, "Frontend", "Technologies: Front-End Architecture", "High-Performance Reactive UI with Dynamic Topological Visualization", "Template: PlayMap #6 (3 Vertical Columns)")

    fe_cols = [
        ("REACT 18 & VITE 6", COLOR_CYAN, [
            "• Component-driven UI with concurrent rendering.",
            "• Ultra-fast Vite HMR build system (<300ms rebuilds).",
            "• Strict TypeScript typing for all telemetry models.",
            "• Optimized bundle size with code splitting."
        ]),
        ("TAILWIND CSS & MOTION", COLOR_GOLD, [
            "• Cyberpunk-sleek dark mode design (#0B1120).",
            "• Responsive glassmorphic panels and metrics cards.",
            "• Fluid micro-animations for live alert pulses.",
            "• Accessible high-contrast status badges."
        ]),
        ("CYTOSCAPE & @XYFLOW", COLOR_BLUE, [
            "• Dynamic topological graph for microservices.",
            "• Real-time node coloring (Green, Amber, Red).",
            "• Causal path tracing and interactive click-through.",
            "• Smooth 60 FPS hardware-accelerated rendering."
        ])
    ]

    col_w = Inches(3.7)
    col_gap = Inches(0.3)
    col_x = Inches(0.8)

    for i, (title, accent, bullets) in enumerate(fe_cols):
        x = col_x + i * (col_w + col_gap)
        card = s7.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), col_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s7.shapes.add_textbox(x + Inches(0.25), Inches(2.2), col_w - Inches(0.5), Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True

        p_t = tf.paragraphs[0]
        p_t.text = title
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(16)
        p_t.font.bold = True
        p_t.font.color.rgb = accent

        for b in bullets:
            p_b = tf.add_paragraph()
            p_b.text = f"\n{b}"
            p_b.font.name = "Calibri"
            p_b.font.size = Pt(11)
            p_b.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s7, "60 FPS Graph Rendering | Instant Live WebSocket Updates | Fully Responsive")
    add_speaker_notes(s7, (
        "Our frontend stack uses React 18 with Vite for concurrent rendering and instant HMR. "
        "TailwindCSS and Framer Motion provide an intuitive dark mode with micro-animations. "
        "For topology, Cytoscape.js and XYFlow render dynamic microservice graphs at 60 FPS with instant visual indicators."
    ))

    # =========================================================================
    # SLIDE 8: Technologies: Back-End (PlayMap #7 3-Column Color Block Style)
    # =========================================================================
    s8 = prs.slides.add_slide(blank_layout)
    apply_background(s8)
    add_header(s8, "Backend", "Technologies: Back-End & Data Pipeline", "High-Throughput Asynchronous Core, In-Memory Streams, and Time-Series Store", "Template: PlayMap #7 (3 Vertical Columns)")

    be_cols = [
        ("PYTHON 3.11 & FASTAPI", COLOR_EMERALD, [
            "• Asynchronous ASGI event loop handling high concurrency.",
            "• Strict Pydantic v2 data validation schemas.",
            "• Native WebSocket server (/ws/telemetry) broadcast.",
            "• Comprehensive Prometheus metrics instrumentation."
        ]),
        ("VALKEY 8.0 & STREAMS", COLOR_ORANGE, [
            "• High-throughput XADD ingestion buffer (O(1) async).",
            "• Decoupled asynchronous worker consumer groups.",
            "• Eliminates ingestion backpressure during traffic spikes.",
            "• Distributed in-memory caching and rate limiting."
        ]),
        ("TIMESCALE DB & POSTGRES", COLOR_PURPLE, [
            "• Hypertables optimized for petabyte-scale time-series logs.",
            "• Continuous aggregates for real-time health analytics.",
            "• Strict multi-tenant isolation with composite foreign keys.",
            "• Declarative lifecycle schema bootstrapping."
        ])
    ]

    for i, (title, accent, bullets) in enumerate(be_cols):
        x = col_x + i * (col_w + col_gap)
        card = s8.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), col_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s8.shapes.add_textbox(x + Inches(0.25), Inches(2.2), col_w - Inches(0.5), Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True

        p_t = tf.paragraphs[0]
        p_t.text = title
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(16)
        p_t.font.bold = True
        p_t.font.color.rgb = accent

        for b in bullets:
            p_b = tf.add_paragraph()
            p_b.text = f"\n{b}"
            p_b.font.name = "Calibri"
            p_b.font.size = Pt(11)
            p_b.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s8, "O(1) Valkey Ingest Buffer | Sub-5ms Hypertable Writes | Zero-Copy Streaming")
    add_speaker_notes(s8, (
        "On the backend, Python 3.11 and FastAPI power our asynchronous API. Valkey 8.0 streams decouple ingestion "
        "from processing, ensuring zero backpressure. TimescaleDB hypertables store billions of time-series logs "
        "with continuous aggregates, allowing rapid analytical lookups without degrading operational throughput."
    ))

    # =========================================================================
    # SLIDE 9: System Architecture & Streaming Pipeline (FlutterForecast #11)
    # =========================================================================
    s9 = prs.slides.add_slide(blank_layout)
    apply_background(s9)
    add_header(s9, "Architecture", "End-to-End System Architecture & Streaming Pipeline", "How Logs Flow from Collectors to Root-Cause Pinpointing (<120ms Latency)", "Template: FlutterForecast #11 (Architecture Diagram)")

    stages = [
        ("1. Collectors", "FluentBit, Vector, OTLP", COLOR_EMERALD),
        ("2. Fast API Ingest", "XADD to Valkey Buffer", COLOR_CYAN),
        ("3. Drain3 Mining", "Cluster & Mask [<*>]", COLOR_BLUE),
        ("4. Feature Windows", "60s Window / 12 Features", COLOR_PURPLE),
        ("5. Isolation Forest", "Outlier Score vs -0.075", COLOR_ORANGE),
        ("6. Causal Graph", "NetworkX Blast Radius", COLOR_EMERALD),
        ("7. WebSocket Push", "React 18 Dashboard", COLOR_CYAN)
    ]

    st_w = Inches(1.5)
    st_gap = Inches(0.18)
    st_x = Inches(0.8)

    for i, (name, detail, accent) in enumerate(stages):
        x = st_x + i * (st_w + st_gap)
        card = s9.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.2), st_w, Inches(1.8))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s9.shapes.add_textbox(x + Inches(0.05), Inches(2.4), st_w - Inches(0.1), Inches(1.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p_n = tf.paragraphs[0]
        p_n.text = name
        p_n.alignment = PP_ALIGN.CENTER
        p_n.font.name = "Trebuchet MS"
        p_n.font.size = Pt(11)
        p_n.font.bold = True
        p_n.font.color.rgb = accent

        p_d = tf.add_paragraph()
        p_d.text = f"\n{detail}"
        p_d.alignment = PP_ALIGN.CENTER
        p_d.font.name = "Calibri"
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = COLOR_SLATE_LIGHT

    # Pipeline summary card below
    pipe_box = s9.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(4.3), Inches(11.733), Inches(2.05))
    pipe_box.fill.solid()
    pipe_box.fill.fore_color.rgb = RGBColor(15, 23, 42)
    pipe_box.line.color.rgb = COLOR_BLUE
    pipe_box.line.width = Pt(1)

    p_tb = s9.shapes.add_textbox(Inches(1.1), Inches(4.4), Inches(11.1), Inches(1.8))
    tf_p = p_tb.text_frame
    tf_p.word_wrap = True

    p_h = tf_p.paragraphs[0]
    p_h.text = "CORE RUNTIME PIPELINE HIGHLIGHTS:"
    p_h.font.name = "Consolas"
    p_h.font.size = Pt(11)
    p_h.font.bold = True
    p_h.font.color.rgb = COLOR_CYAN

    p_c = tf_p.add_paragraph()
    p_c.text = (
        "• Non-Blocking Ingestion: Endpoints (/ingest-log, /v1/logs) respond in <2ms with 202 Accepted, queuing records in Valkey.\n"
        "• High-Density Compression: Drain3 maintains an online prefix tree, compressing repetitive strings by 95%+ in sub-milliseconds.\n"
        "• Sliding-Window Vectorization: Windows close dynamically (interval=10s, window=60s, stride=30s), computing entropy and burst rates.\n"
        "• Instant Incident Broadcast: When an anomaly triggers, NetworkX calculates graph reachability and WebSockets push the update in <120ms."
    )
    p_c.font.name = "Calibri"
    p_c.font.size = Pt(10)
    p_c.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s9, "<120ms End-to-End Latency | 7 Pipelined Stages | Completely Asynchronous")
    add_speaker_notes(s9, (
        "Here is the data flow end-to-end: Logs arrive via OTLP or Fluent Bit into FastAPI. Instead of hitting the database "
        "synchronously, they enter an in-memory Valkey stream. Our Drain3 worker parses them into templates online. "
        "The feature extractor groups them into 60-second sliding windows. Our Isolation Forest scores the vector, and "
        "if anomalous, the NetworkX graph engine ranks which upstream service triggered the cascade in under 120 milliseconds."
    ))

    # =========================================================================
    # SLIDE 10: Algorithmic Core: Drain3 & Isolation Forest (FlutterForecast #12-13)
    # =========================================================================
    s10 = prs.slides.add_slide(blank_layout)
    apply_background(s10)
    add_header(s10, "ML Core", "Algorithmic Core: Drain3 & Isolation Forest", "Online Template Mining & Multidimensional Feature Anomaly Scoring", "Template: FlutterForecast #12-13 (3 Components)")

    alg_components = [
        ("01", "Drain3 Template Miner", "Online Prefix-Tree Parser", (
            "• Parses raw unstructured text into structured templates in <0.5ms.\n\n"
            "• Dynamically identifies dynamic parameters (IPs, UUIDs, IDs) and replaces them with [<*>] wildcards.\n\n"
            "• Achieves 95%+ compression ratio while preserving full diagnostic context."
        ), COLOR_EMERALD),
        ("02", "Sliding-Window Features", "12-Dimensional Vector", (
            "• 60s windows with 30s stride extract 12 statistical and entropy features:\n\n"
            "• Statistical: log_count, error_count, warning_count, error_ratio, logs_per_sec.\n\n"
            "• Entropy & Dynamics: template_entropy, unique_templates, dominant_service_ratio, burst_indicator."
        ), COLOR_CYAN),
        ("03", "Isolation Forest Model", "Optimal Decision Boundary", (
            "• Ensemble of 100 isolation trees isolating outliers based on path length.\n\n"
            "• Optimal decision threshold at -0.075 achieves 81.25% precision and 86.67% recall.\n\n"
            "• 0.959 ROC-AUC score discriminating real incidents from normal traffic."
        ), COLOR_PURPLE)
    ]

    for i, (num, title, subtitle, content, accent) in enumerate(alg_components):
        x = c_start_x + i * (c_width + c_gap)
        card = s10.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), c_width, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        badge = s10.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.3), Inches(2.2), Inches(0.6), Inches(0.6))
        badge.fill.solid()
        badge.fill.fore_color.rgb = accent
        badge.line.fill.background()
        b_tb = s10.shapes.add_textbox(x + Inches(0.3), Inches(2.25), Inches(0.6), Inches(0.5))
        b_tb.text_frame.paragraphs[0].text = num
        b_tb.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        b_tb.text_frame.paragraphs[0].font.name = "Consolas"
        b_tb.text_frame.paragraphs[0].font.size = Pt(14)
        b_tb.text_frame.paragraphs[0].font.bold = True
        b_tb.text_frame.paragraphs[0].font.color.rgb = COLOR_WHITE

        tb = s10.shapes.add_textbox(x + Inches(0.3), Inches(3.0), c_width - Inches(0.6), Inches(3.1))
        tf = tb.text_frame
        tf.word_wrap = True

        p1 = tf.paragraphs[0]
        p1.text = title
        p1.font.name = "Trebuchet MS"
        p1.font.size = Pt(16)
        p1.font.bold = True
        p1.font.color.rgb = COLOR_WHITE

        p2 = tf.add_paragraph()
        p2.text = subtitle
        p2.font.name = "Calibri"
        p2.font.size = Pt(11)
        p2.font.italic = True
        p2.font.color.rgb = accent

        p3 = tf.add_paragraph()
        p3.text = f"\n{content}"
        p3.font.name = "Calibri"
        p3.font.size = Pt(10)
        p3.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s10, "0.959 ROC-AUC | 0.839 F1-Score | 12 Vector Features | 100 Trees")
    add_speaker_notes(s10, (
        "Our algorithmic engine has 3 components matching FlutterForecast's architecture: "
        "Component 01 is Drain3, an online tree that strips variables and creates semantic templates in sub-milliseconds. "
        "Component 02 is our 12-feature extractor tracking Shannon entropy and error acceleration. "
        "Component 03 is our Isolation Forest. Sweeping decision thresholds from -0.800 to 0.000 proved that -0.075 "
        "delivers 81.25% precision, 86.67% recall, and an outstanding 0.959 ROC-AUC."
    ))

    # =========================================================================
    # SLIDE 11: Dynamic Causal Graph & Blast-Radius Ranking
    # =========================================================================
    s11 = prs.slides.add_slide(blank_layout)
    apply_background(s11)
    add_header(s11, "Graph Engine", "Dynamic Causal Topology & Blast-Radius Ranking", "Tracing Cascading Failures to Their True Upstream Origin", "Template: FlutterForecast #12 & PlayMap #8")

    graph_pillars = [
        ("Dynamic Topology Autodiscovery", COLOR_CYAN, (
            "• Service dependencies are discovered continuously from live incoming traces and request timestamps.\n\n"
            "• NetworkX directed graph edges automatically reflect the live architecture without manual documentation.\n\n"
            "• Adapts instantly to autoscaling microservice clusters and canary deployments."
        )),
        ("Root-Cause vs. Victim Separation", COLOR_ORANGE, (
            "• Temporal Lead-Time Analysis detects which service degraded first before downstream errors surfaced.\n\n"
            "• Distinguishes true root cause (e.g. auth DB exhaustion) from victim services throwing 504 timeouts.\n\n"
            "• Suppresses over 85% of redundant symptom alerts, cutting alert storms."
        )),
        ("Quantitative Blast-Radius Scoring", COLOR_EMERALD, (
            "• Traverses reachability pathways from the anomalous service node to calculate impact percentage.\n\n"
            "• Scores severity based on affected dependencies (e.g. 66% blast radius over Order & Payment services).\n\n"
            "• Pushes real-time graph highlight to the React dashboard with red causal indicators."
        ))
    ]

    for i, (title, accent, content) in enumerate(graph_pillars):
        x = col_x + i * (col_w + col_gap)
        card = s11.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), col_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s11.shapes.add_textbox(x + Inches(0.25), Inches(2.3), col_w - Inches(0.5), Inches(3.8))
        tf = tb.text_frame
        tf.word_wrap = True

        p_t = tf.paragraphs[0]
        p_t.text = title
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(15)
        p_t.font.bold = True
        p_t.font.color.rgb = accent

        p_c = tf.add_paragraph()
        p_c.text = f"\n{content}"
        p_c.font.name = "Calibri"
        p_c.font.size = Pt(11)
        p_c.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s11, "100% Causal Dependency Autodiscovery | 85%+ Redundant Alert Suppression")
    add_speaker_notes(s11, (
        "When an incident strikes, the hardest question is: 'Who started it?' Downstream services fail simply because "
        "their upstream dependencies stalled. LogSentinel constructs a dynamic directed graph. By combining temporal anomaly "
        "onset with dependency topology, it pinpoints the true root-cause service, measures the blast radius, and suppresses "
        "85% of downstream symptom alerts."
    ))

    # =========================================================================
    # SLIDE 12: Problem-Solution Fit Matrix (PlayMap #9 Mapped Rows Style)
    # =========================================================================
    s12 = prs.slides.add_slide(blank_layout)
    apply_background(s12)
    add_header(s12, "Fit Matrix", "Problem-Solution Fit Matrix", "Directly Addressing Operational Bottlenecks with Algorithmic Solutions", "Template: PlayMap #9 (Problem -> Solution Mapped Rows)")

    rows = [
        (
            "Unknown Bugs & Zero-Day Incidents",
            "Unsupervised Isolation Forest",
            "Problem: Traditional monitoring relies on brittle regex rules. Unseen exceptions go undetected until users report outages.\n"
            "LogSentinel Fit: Unsupervised Isolation Forest evaluates structural features and entropy shifts, detecting novel zero-day anomalies without historical training labels.",
            COLOR_ORANGE, COLOR_EMERALD
        ),
        (
            "Cascading Outages & Alert Fatigue",
            "Dynamic Causal Topology Ranking",
            "Problem: A core service failure triggers dozens of downstream microservices to spam SREs with 504 Gateway Timeouts.\n"
            "LogSentinel Fit: NetworkX graph traversal isolates the upstream root node and ranks blast radius, suppressing symptom alarms and restoring triage focus.",
            COLOR_PURPLE, COLOR_CYAN
        ),
        (
            "Ballooning Cloud Log Storage Bills",
            "Drain3 Real-Time Template Mining",
            "Problem: Indexing and storing terabytes of repetitive raw log strings causes exorbitant cloud bills and sluggish dashboard searches.\n"
            "LogSentinel Fit: Drain3 clusters logs into templates and stores only dynamic variables, achieving 95%+ compression while preserving 100% forensic fidelity.",
            COLOR_BLUE, COLOR_GOLD
        )
    ]

    r_y = Inches(2.0)
    r_h = Inches(1.35)
    r_gap = Inches(0.18)

    for i, (p_title, s_title, desc, p_color, s_color) in enumerate(rows):
        y = r_y + i * (r_h + r_gap)
        card = s12.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), y, Inches(11.733), r_h)
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = RGBColor(30, 41, 59)
        card.line.width = Pt(1)

        # Header badges: Problem -> Solution
        tb = s12.shapes.add_textbox(Inches(1.0), y + Inches(0.1), Inches(11.3), Inches(0.4))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        r1 = p.add_run()
        r1.text = f"{p_title.upper()}   ──────►   "
        r1.font.name = "Trebuchet MS"
        r1.font.size = Pt(12)
        r1.font.bold = True
        r1.font.color.rgb = p_color

        r2 = p.add_run()
        r2.text = s_title.upper()
        r2.font.name = "Trebuchet MS"
        r2.font.size = Pt(12)
        r2.font.bold = True
        r2.font.color.rgb = s_color

        # Description
        tb_d = s12.shapes.add_textbox(Inches(1.0), y + Inches(0.48), Inches(11.3), Inches(0.8))
        tf_d = tb_d.text_frame
        tf_d.word_wrap = True
        p_d = tf_d.paragraphs[0]
        p_d.text = desc
        p_d.font.name = "Calibri"
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s12, "95%+ Storage Compression | Zero-Day Anomaly Capture | Instant Root-Cause Pinpointing")
    add_speaker_notes(s12, (
        "This matrix demonstrates our exact problem-solution fit: For unknown bugs, our unsupervised model catches novel "
        "anomalies without rules. For cascading outages, our causal graph silences symptom alarms and highlights the origin. "
        "And for soaring log bills, Drain3 compresses data by 95% while retaining full diagnostic detail."
    ))

    # =========================================================================
    # SLIDE 13: Live Demonstration & Chaos Scenario (PlayMap #8 & FlutterForecast #14)
    # =========================================================================
    s13 = prs.slides.add_slide(blank_layout)
    apply_background(s13)
    add_header(s13, "Demo", "Live Demonstration & Chaos Engineering Scenario", "Real-Time Detection of Cascading Microservice Failure (scripts/trigger_demo_incident.py)", "Template: PlayMap #8 (Demo Cycle) / FlutterForecast #14")

    demo_steps = [
        ("Step 1", "Normal Baseline Traffic", "500 logs/sec streaming across auth, order, and payment services. All graph nodes green; anomaly score well below -0.075.", COLOR_EMERALD),
        ("Step 2", "Simulated Chaos Injection", "Chaos script injects connection pool exhaustion and latency into auth-service; downstream services start logging 504 errors.", COLOR_ORANGE),
        ("Step 3", "Algorithmic Detection", "Drain3 flags new error templates; sliding-window error_ratio spikes past 0.15; Isolation Forest flags anomaly.", COLOR_PURPLE),
        ("Step 4", "Live UI Reaction", "WebSocket pushes incident alert: auth-service node flashes red, ranked #1 root cause with 66% blast radius across the fleet.", COLOR_CYAN)
    ]

    d_w = Inches(2.7)
    d_gap = Inches(0.31)
    d_x = Inches(0.8)

    for i, (step, title, desc, accent) in enumerate(demo_steps):
        x = d_x + i * (d_w + d_gap)
        card = s13.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), d_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s13.shapes.add_textbox(x + Inches(0.2), Inches(2.2), d_w - Inches(0.4), Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True

        p_s = tf.paragraphs[0]
        p_s.text = step.upper()
        p_s.font.name = "Consolas"
        p_s.font.size = Pt(12)
        p_s.font.bold = True
        p_s.font.color.rgb = accent

        p_t = tf.add_paragraph()
        p_t.text = f"\n{title}"
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(15)
        p_t.font.bold = True
        p_t.font.color.rgb = COLOR_WHITE

        p_d = tf.add_paragraph()
        p_d.text = f"\n{desc}"
        p_d.font.name = "Calibri"
        p_d.font.size = Pt(10.5)
        p_d.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s13, "<120ms Broadcast Delay | 100% Chaos Repeatability | Interactive Live UI")
    add_speaker_notes(s13, (
        "In our live demo, we run our chaos script to simulate a realistic outage: We inject database pool exhaustion into "
        "auth-service. As payment and order services throw downstream timeouts, LogSentinel's feature worker detects the entropy "
        "anomaly within seconds. On the React dashboard, auth-service turns flashing red as the #1 root cause with a 66% blast radius."
    ))

    # =========================================================================
    # SLIDE 14: Added Value & Strategic ROI Breakdown (PlayMap #10 5x 20% Wheel)
    # =========================================================================
    s14 = prs.slides.add_slide(blank_layout)
    apply_background(s14)
    add_header(s14, "Value", "Added Value & Strategic ROI Breakdown", "Project Focus: Autonomous, Zero-Configuration Microservice Observability", "Template: PlayMap #10 (5x 20% Value Distribution)")

    roi_cards = [
        ("20%", "MTTR Reduction", "Cuts diagnosis time from 45+ minutes to under 30 seconds.", COLOR_EMERALD),
        ("20%", "Alert Noise Filter", "Suppresses over 85% of redundant cascading symptom alerts.", COLOR_CYAN),
        ("20%", "Zero-Rule ML", "Unsupervised learning eliminates brittle regex and manual rule upkeep.", COLOR_BLUE),
        ("20%", "Cloud Scalability", "10,000+ logs/sec per node with ultra-low memory (<52MB RSS).", COLOR_PURPLE),
        ("20%", "Dynamic Visibility", "Live service dependency graph discovered automatically from logs.", COLOR_ORANGE)
    ]

    for i, (pct, title, desc, accent) in enumerate(roi_cards):
        x = p_x + i * (p_w + p_gap)
        card = s14.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), p_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s14.shapes.add_textbox(x + Inches(0.15), Inches(2.3), p_w - Inches(0.3), Inches(3.8))
        tf = tb.text_frame
        tf.word_wrap = True

        p_pct = tf.paragraphs[0]
        p_pct.text = pct
        p_pct.alignment = PP_ALIGN.CENTER
        p_pct.font.name = "Trebuchet MS"
        p_pct.font.size = Pt(36)
        p_pct.font.bold = True
        p_pct.font.color.rgb = accent

        p_t = tf.add_paragraph()
        p_t.text = title
        p_t.alignment = PP_ALIGN.CENTER
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(13)
        p_t.font.bold = True
        p_t.font.color.rgb = COLOR_WHITE

        p_d = tf.add_paragraph()
        p_d.text = f"\n{desc}"
        p_d.alignment = PP_ALIGN.CENTER
        p_d.font.name = "Calibri"
        p_d.font.size = Pt(10)
        p_d.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s14, "5 Strategic Value Pillars (20% Each) | 85% Noise Reduction | 90% MTTR Reduction")
    add_speaker_notes(s14, (
        "Matching PlayMap's added value wheel, LogSentinel's ROI is evenly divided into 5 critical pillars of 20% each: "
        "A 90% reduction in MTTR; 85% suppression of alert noise; zero-rule unsupervised ML; cloud scalability handling "
        "10k logs/sec; and continuous dynamic topology autodiscovery."
    ))

    # =========================================================================
    # SLIDE 15: Business Model: Tiered Platform Pricing (PlayMap #11 & FlutterForecast #5)
    # =========================================================================
    s15 = prs.slides.add_slide(blank_layout)
    apply_background(s15)
    add_header(s15, "Pricing", "Business Model: Tiered Platform Pricing", "Open-Source Core with Scalable Cloud and Enterprise Tiers", "Template: PlayMap #11 (3 Tier Cards) / FlutterForecast #5")

    tiers = [
        ("COMMUNITY", "Free ($0)", "Apache 2.0 Open Source", [
            "• Turnkey Docker Compose fleet.",
            "• Drain3 online template miner.",
            "• Sliding-window Isolation Forest.",
            "• Single-tenant deployment.",
            "• Up to 1,000 logs/sec ingestion.",
            "• Community GitHub & Discord support."
        ], COLOR_SLATE_MUTED),
        ("LOGSENTINEL PRO", "$49 / node / mo", "Scaleups & SRE Teams", [
            "• Everything in Community, plus:",
            "• Valkey stream cluster (25k logs/sec/node).",
            "• Dynamic Causal Graph & Blast Radius.",
            "• Multi-tenant data isolation.",
            "• Automated model retraining schedule.",
            "• Slack & PagerDuty webhook alerts."
        ], COLOR_EMERALD),
        ("ENTERPRISE SENTINEL", "$1,499+ / mo", "Enterprise Cloud & FinTech", [
            "• Everything in Pro, plus:",
            "• eBPF kernel-level tracing collector.",
            "• Enterprise SSO (Microsoft & Google OAuth).",
            "• Air-gapped VPC / on-prem support.",
            "• Valkey High-Availability cluster.",
            "• 24/7 dedicated SRE support & 99.99% SLA."
        ], COLOR_PURPLE)
    ]

    for i, (name, price, sub, features, accent) in enumerate(tiers):
        x = col_x + i * (col_w + col_gap)
        card = s15.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), col_w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(2.0 if i == 1 else 1.0)

        tb = s15.shapes.add_textbox(x + Inches(0.25), Inches(2.2), col_w - Inches(0.5), Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True

        p_name = tf.paragraphs[0]
        p_name.text = name
        p_name.font.name = "Trebuchet MS"
        p_name.font.size = Pt(13)
        p_name.font.bold = True
        p_name.font.color.rgb = accent

        p_price = tf.add_paragraph()
        p_price.text = price
        p_price.font.name = "Trebuchet MS"
        p_price.font.size = Pt(24)
        p_price.font.bold = True
        p_price.font.color.rgb = COLOR_WHITE

        p_sub = tf.add_paragraph()
        p_sub.text = sub
        p_sub.font.name = "Calibri"
        p_sub.font.size = Pt(10)
        p_sub.font.italic = True
        p_sub.font.color.rgb = COLOR_SLATE_MUTED

        for f in features:
            p_f = tf.add_paragraph()
            p_f.text = f
            p_f.font.name = "Calibri"
            p_f.font.size = Pt(10)
            p_f.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s15, "Free Open-Source Core | $49/node Pro Tier | $1,499+ Enterprise Contract")
    add_speaker_notes(s15, (
        "Our business model blends product-led open source growth with enterprise monetization: "
        "The Community Edition is free under Apache 2.0. LogSentinel Pro at $49/node unlocks our causal graph, "
        "multi-tenancy, and automated retraining. Enterprise Sentinel starts at $1,499/month, adding eBPF tracing, "
        "native Microsoft and Google SSO, air-gapped deployment, and 24/7 SLAs."
    ))

    # =========================================================================
    # SLIDE 16: Business Model: Add-ons & Boosters (PlayMap #12 Shop & Boosters)
    # =========================================================================
    s16 = prs.slides.add_slide(blank_layout)
    apply_background(s16)
    add_header(s16, "Add-Ons", "Business Model: Add-ons & Professional Services", "Modular Cloud Boosters & High-Value Enterprise Advisory Engagements", "Template: PlayMap #12 (In-Website Shop & Boosters)")

    addons = [
        ("CLOUD BOOSTERS & INTEGRATIONS", "$199 - $499 / month", COLOR_CYAN, [
            "• SIEM Bi-Directional Bridge ($199/mo): Forward parsed Drain3 templates and scored anomaly events directly into Splunk or Datadog.",
            "• Long-Term Cold Storage Compactor ($299/mo): S3 and GCS Parquet archiving with 99% query compression and continuous indexing.",
            "• CloudWatch & GCP Logging Sinks ($199/mo): Native 1-click cloud provider audit log collectors with zero manual setup.",
            "• High-Frequency Anomaly Retraining Packs ($499/mo): Hourly adaptive model updating for rapidly evolving microservice clusters."
        ]),
        ("SRE ADVISORY & PROFESSIONAL SERVICES", "$2,500 - $10,000 / engagement", COLOR_GOLD, [
            "• Custom Domain Anomaly Calibration ($2,500): Tuning Isolation Forest contamination and hyper-parameters for proprietary protocols.",
            "• Chaos Engineering Readiness Audit ($5,000): Simulated failure game days to validate resilience and verify root-cause detection speed.",
            "• Production Cutover Assistance ($7,500): 7-day guided rollout with zero-downtime migration runbooks and disaster recovery setup.",
            "• Dedicated Technical Account Manager ($10,000/yr): Named distributed systems architect with priority 1-hour SLA response."
        ])
    ]

    card_2w = Inches(5.7)
    card_2gap = Inches(0.33)
    card_2x = Inches(0.8)

    for i, (title, price_range, accent, items) in enumerate(addons):
        x = card_2x + i * (card_2w + card_2gap)
        card = s16.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), card_2w, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s16.shapes.add_textbox(x + Inches(0.3), Inches(2.2), card_2w - Inches(0.6), Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True

        p_t = tf.paragraphs[0]
        p_t.text = title
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(15)
        p_t.font.bold = True
        p_t.font.color.rgb = accent

        p_p = tf.add_paragraph()
        p_p.text = price_range
        p_p.font.name = "Calibri"
        p_p.font.size = Pt(12)
        p_p.font.bold = True
        p_p.font.color.rgb = COLOR_WHITE

        for it in items:
            p_it = tf.add_paragraph()
            p_it.text = f"\n{it}"
            p_it.font.name = "Calibri"
            p_it.font.size = Pt(10)
            p_it.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s16, "$199-$499 Recurring Add-ons | $2.5k-$10k High-Margin Services | Expand Net Revenue")
    add_speaker_notes(s16, (
        "To expand net retention and accelerate enterprise deployment, we offer modular boosters and advisory services: "
        "Boosters range from $199 to $499 a month for cold-storage S3 compactors and SIEM bridges. For enterprise cutovers, "
        "our team provides SRE advisory services—calibrating custom models and conducting chaos game days ranging from "
        "$2,500 to $10,000 per engagement."
    ))

    # =========================================================================
    # SLIDE 17: Future Roadmap & Horizons (FlutterForecast #16 Future Plans)
    # =========================================================================
    s17 = prs.slides.add_slide(blank_layout)
    apply_background(s17)
    add_header(s17, "Roadmap", "Future Roadmap & Innovation Horizon", "The Next Frontier: Autonomous Self-Healing Observability", "Template: FlutterForecast #16 (Future Plans)")

    roadmap = [
        ("Q1 2027", "eBPF Kernel-Level Tracing", (
            "• Zero-overhead distributed tracing directly at the Linux socket layer.\n\n"
            "• Bypasses application code modification and language SDK agents.\n\n"
            "• Correlates network latency, packet loss, and gRPC payloads automatically."
        ), COLOR_CYAN),
        ("Q2 2027", "GenAI Post-Mortems & PRs", (
            "• Autonomous LLM synthesis of anomaly windows into executive incident reports.\n\n"
            "• Automatically generates GitHub Pull Requests suggesting bug fixes or config tweaks.\n\n"
            "• Slashes incident post-mortem drafting from 4 hours to 30 seconds."
        ), COLOR_PURPLE),
        ("Q3 2027", "Autonomous Self-Healing", (
            "• Closed-loop Kubernetes remediation when anomaly confidence exceeds 95%.\n\n"
            "• Automatically triggers pod restarts, traffic shedding, or circuit breaking.\n\n"
            "• Eliminates human on-call fatigue for recurring, known failure signatures."
        ), COLOR_EMERALD)
    ]

    for i, (timeline, title, content, accent) in enumerate(roadmap):
        x = c_start_x + i * (c_width + c_gap)
        card = s17.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(2.0), c_width, Inches(4.3))
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = accent
        card.line.width = Pt(1.5)

        tb = s17.shapes.add_textbox(x + Inches(0.3), Inches(2.3), c_width - Inches(0.6), Inches(3.8))
        tf = tb.text_frame
        tf.word_wrap = True

        p_time = tf.paragraphs[0]
        p_time.text = timeline
        p_time.font.name = "Consolas"
        p_time.font.size = Pt(14)
        p_time.font.bold = True
        p_time.font.color.rgb = accent

        p_t = tf.add_paragraph()
        p_t.text = title
        p_t.font.name = "Trebuchet MS"
        p_t.font.size = Pt(16)
        p_t.font.bold = True
        p_t.font.color.rgb = COLOR_WHITE

        p_c = tf.add_paragraph()
        p_c.text = f"\n{content}"
        p_c.font.name = "Calibri"
        p_c.font.size = Pt(11)
        p_c.font.color.rgb = COLOR_SLATE_LIGHT

    add_bottom_metric_bar(s17, "Zero-Code eBPF Tracing | Automated GenAI Post-Mortems | Closed-Loop Auto-Remediation")
    add_speaker_notes(s17, (
        "Looking forward, our roadmap expands LogSentinel into an autonomous self-healing platform: "
        "In Q1 2027, zero-overhead eBPF kernel tracing removes the need for SDK instrumentation. "
        "In Q2, Generative AI incident post-mortems will draft post-mortems and pull requests automatically. "
        "And in Q3, our Kubernetes Operator will close the loop with automated remediation—restarting failing pods before "
        "users even notice."
    ))

    # =========================================================================
    # SLIDE 18: Thank You & Contact (PlayMap #13 & FlutterForecast #17)
    # =========================================================================
    s18 = prs.slides.add_slide(blank_layout)
    apply_background(s18)

    # Big Thank You
    ty_box = s18.shapes.add_textbox(Inches(1.0), Inches(1.2), Inches(11.333), Inches(1.2))
    tf_ty = ty_box.text_frame
    p_ty = tf_ty.paragraphs[0]
    p_ty.text = "Thank You!"
    p_ty.alignment = PP_ALIGN.CENTER
    p_ty.font.name = "Trebuchet MS"
    p_ty.font.size = Pt(56)
    p_ty.font.bold = True
    p_ty.font.color.rgb = COLOR_WHITE

    # Slogan pill
    pill18 = s18.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(3.4), Inches(2.6), Inches(6.5), Inches(0.65))
    pill18.fill.solid()
    pill18.fill.fore_color.rgb = COLOR_ORANGE
    pill18.line.fill.background()

    p18_tb = s18.shapes.add_textbox(Inches(3.4), Inches(2.65), Inches(6.5), Inches(0.5))
    tf_p18 = p18_tb.text_frame
    p_p18 = tf_p18.paragraphs[0]
    p_p18.text = "Change starts with the steps we take today!"
    p_p18.alignment = PP_ALIGN.CENTER
    p_p18.font.name = "Trebuchet MS"
    p_p18.font.size = Pt(14)
    p_p18.font.bold = True
    p_p18.font.color.rgb = COLOR_WHITE

    # Subtitle
    sub18 = s18.shapes.add_textbox(Inches(1.5), Inches(3.4), Inches(10.333), Inches(0.5))
    tf_sub18 = sub18.text_frame
    p_sub18 = tf_sub18.paragraphs[0]
    p_sub18.text = "Empower your SREs. Eliminate alert noise. Tame microservice chaos."
    p_sub18.alignment = PP_ALIGN.CENTER
    p_sub18.font.name = "Calibri"
    p_sub18.font.size = Pt(16)
    p_sub18.font.color.rgb = COLOR_CYAN

    # Links card
    links_card = s18.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(2.5), Inches(4.2), Inches(8.333), Inches(2.2))
    links_card.fill.solid()
    links_card.fill.fore_color.rgb = COLOR_CARD
    links_card.line.color.rgb = COLOR_BLUE
    links_card.line.width = Pt(1)

    l_tb = s18.shapes.add_textbox(Inches(2.8), Inches(4.35), Inches(7.7), Inches(1.9))
    tf_l = l_tb.text_frame
    tf_l.word_wrap = True

    p_l1 = tf_l.paragraphs[0]
    p_l1.text = "PROJECT RESOURCES & CONTACT:"
    p_l1.font.name = "Consolas"
    p_l1.font.size = Pt(12)
    p_l1.font.bold = True
    p_l1.font.color.rgb = COLOR_EMERALD

    p_l2 = tf_l.add_paragraph()
    p_l2.text = (
        "• GitHub Repository: github.com/meloshaj/LogSentinel\n"
        "• License: Apache 2.0 Open Source\n"
        "• Documentation: /docs (Runbooks, Feature Extraction, Architecture ADRs)\n"
        "• Team Contact: Melos Hajrullahu (hajrullahumelos@gmail.com)\n"
        "• Ready to run: docker compose -f docker-compose.demo.yml up --build -d"
    )
    p_l2.font.name = "Calibri"
    p_l2.font.size = Pt(11)
    p_l2.font.color.rgb = COLOR_SLATE_LIGHT

    add_speaker_notes(s18, (
        "Thank you so much for listening. LogSentinel is open source under Apache 2.0, with turnkey Docker Compose "
        "scripts ready to run immediately. Clone the repo, trigger our demo chaos incident, and experience the future "
        "of autonomous observability firsthand. We would now love to open the floor to any questions!"
    ))

    # Save PPTX Presentation
    pptx_path = os.path.join("presentation", "LogSentinel_Presentation_GoogleSlides.pptx")
    prs.save(pptx_path)
    print(f"Successfully generated Google Slides presentation (.pptx) at: {pptx_path}")

if __name__ == "__main__":
    create_google_slides_deck()
