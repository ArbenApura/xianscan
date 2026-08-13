// TYPESETTING — RENDER TRANSLATED TEXT ONTO THE CLEANED PAGE WITH @napi-rs/canvas (SKIA).
import { createCanvas, GlobalFonts, loadImage, type Image } from '@napi-rs/canvas';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

// -- TYPES -- //

export interface TypesetBox {
	x: number;
	y: number;
	w: number;
	h: number;
}

export interface TypesetRegion {
	id: string;
	box: TypesetBox;
	text: string;
	category: 'dialogue' | 'sfx' | 'mono' | 'other';
	vertical?: boolean;
}

export interface TextColor {
	fill: string;
	stroke: string;
}

// -- STAT-PANEL SEGMENT TYPES -- //

export type SegmentKind = 'title' | 'rarity' | 'subtitle' | 'body' | 'flavour';

export interface TextSegment {
	kind: SegmentKind;
	text: string;
}

// -- CONSTANTS -- //

const FONT_DIR = fileURLToPath(new URL('../../../static/fonts', import.meta.url));

export const FONT_DIALOGUE = 'CC Wild Words';
export const FONT_SFX = 'CC Wild Words';
export const FONT_MONO = 'CC Wild Words';

// RENDER MARGINS INSIDE THE DETECTED BOX — 10% INSET ENSURES TEXT STAYS INSIDE CURVED BUBBLE EDGES
const BOX_INSET = 0.10;
const MAX_LINES = 8;
const MIN_FONT_SIZE = 8;
const LINE_HEIGHT = 1.2;
// ABSOLUTE FONT-SIZE CAP FOR DIALOGUE / MONO REGIONS — PREVENTS A LARGE DETECTED BOX
// (e.g. A MULTI-LINE BUBBLE WHOSE UNION BOX SPANS MOST OF THE PAGE WIDTH) FROM INFLATING
// THE TEXT TO FILL THE BOX. SFX IS DELIBERATELY EXCLUDED — BIG SFX IS INTENTIONAL.
const MAX_DIALOGUE_FONT_SIZE = 42;
// SFX CAN LEGITIMATELY BE LARGE (IMPACT TEXT), BUT AN UNCAPPED BOX-DERIVED SIZE PRODUCES
// ABSURD RESULTS WHEN THE REGION BOX IS OVERSIZED (e.g. A WIDE GROUPED PARAGRAPH THAT
// CLASSIFY_REGION MISLABELS AS SFX). CAP AT A GENEROUS BUT SANE MAXIMUM.
const MAX_SFX_FONT_SIZE = 100;

// KEYWORDS THAT MARK A LINE AS A RARITY+TYPE LINE
const RARITY_KEYWORDS = new Set([
	'LEGENDARY', 'MYTHIC', 'DIVINE', 'EPIC', 'RARE', 'FINE', 'UNCOMMON', 'COMMON',
	'TRANSCENDENT', 'IMMORTAL', 'SACRED', 'ANCIENT', 'UNIQUE',
]);

let fontsRegistered = false;

function registerFonts(): void {
	if (fontsRegistered) return;
	fontsRegistered = true;
	GlobalFonts.registerFromPath(join(FONT_DIR, 'CCWildWords-Roman.ttf'), FONT_DIALOGUE);
	if (!GlobalFonts.has(FONT_DIALOGUE) || !GlobalFonts.has(FONT_SFX) || !GlobalFonts.has(FONT_MONO)) {
		fontsRegistered = false;
		throw new Error(`typeset fonts not found in ${FONT_DIR} — run the font download step`);
	}
}

export function fontFor(category: TypesetRegion['category']): string {
	return category === 'sfx' ? FONT_SFX : FONT_DIALOGUE;
}

// -- COLOR / CONTRAST (PURE) -- //

function luminance(r: number, g: number, b: number): number {
	const lin = (c: number) => {
		const s = c / 255;
		return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
	};
	return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function pickTextColor(bg: { r: number; g: number; b: number }): TextColor {
	return luminance(bg.r, bg.g, bg.b) < 0.18
		? { fill: 'white', stroke: 'black' }
		: { fill: 'black', stroke: 'white' };
}


// -- STAT-PANEL PARSING (PURE) -- //

/**
 * Detect whether `text` is a wuxia stat-panel block (starts with a [TITLE] line).
 * Returns an ordered array of segments; returns null if not a stat-panel.
 */
export function parseStatPanel(text: string): TextSegment[] | null {
	const rawLines = text.split('\n').map((l) => l.trim()).filter(Boolean);
	if (rawLines.length === 0) return null;

	// -- CASE 1a: starts with 【TITLE】 CJK brackets (LLM now outputs this directly) --
	// -- CASE 1b: starts with [TITLE] ASCII brackets (fallback — convert to CJK to avoid font arrow remapping) --
	const hasCjkBracket = /^【.+】$/.test(rawLines[0]);
	const hasAsciiBracket = /^\[.+\]$/.test(rawLines[0]);
	if (hasCjkBracket || hasAsciiBracket) {
		// Strip the bracket characters entirely — CC Wild Words cannot render either
		// ASCII [ ] (maps to arrows) or CJK 【】 (maps to empty squares / tofu).
		// Visual prominence of the title comes from large font size + top position.
		const titleText = rawLines[0]
			.replace(/^[【\[]/, '')
			.replace(/[】\]]$/, '')
			.trim();
		const segments: TextSegment[] = [
			{ kind: 'title', text: titleText },
		];
		for (let i = 1; i < rawLines.length; i++) {
			segments.push(..._classifyLine(rawLines[i]));
		}
		return segments;
	}

	// -- CASE 2: starts with a rarity keyword (body region — no title prefix) --
	// The OCR may split the title and the body into separate regions. The body region
	// looks like: "LEGENDARY WAR CHARIOT\n(IMPROVED VERSION)\nForged by…"
	const firstWord = rawLines[0].split(/\s+/)[0].toUpperCase();
	if (RARITY_KEYWORDS.has(firstWord) && rawLines.length >= 2) {
		return rawLines.map((l) => _classifyLine(l)[0]);
	}

	return null;
}


/** Classify one line into the right segment kind. */
function _classifyLine(line: string): TextSegment[] {
	if (/^\(.+\)$/.test(line)) return [{ kind: 'subtitle', text: line }];
	if (line.startsWith('*')) return [{ kind: 'flavour', text: line }];
	const fw = line.split(/\s+/)[0].toUpperCase();
	if (RARITY_KEYWORDS.has(fw)) return [{ kind: 'rarity', text: line.toUpperCase() }];
	return [{ kind: 'body', text: line }];
}

// -- LAYOUT (PURE, CANVAS-MEASURED) -- //

export function wrapText(ctx: { measureText(t: string): { width: number } }, text: string, maxWidth: number): string[] {
	const lines: string[] = [];
	for (const paragraph of text.split('\n')) {
		let current = '';
		for (const word of paragraph.split(/\s+/)) {
			if (!word) continue;
			const candidate = current ? `${current} ${word}` : word;
			if (ctx.measureText(candidate).width <= maxWidth || !current) {
				current = candidate;
				while (ctx.measureText(current).width > maxWidth && current.length > 1) {
					lines.push(current.slice(0, -1));
					current = current.slice(-1);
				}
			} else {
				lines.push(current);
				current = word;
			}
		}
		if (current) lines.push(current);
		if (lines.length >= MAX_LINES) break;
	}
	return lines.slice(0, MAX_LINES);
}

/**
 * Balanced word-wrap: distributes words evenly across lines so no line is
 * disproportionately short. Uses the same greedy algorithm but binary-searches
 * for the narrowest target width that still produces the same number of lines
 * as a full-width greedy wrap. The result looks typeset rather than ragged.
 */
export function balancedWrapText(
	ctx: { measureText(t: string): { width: number } },
	text: string,
	maxWidth: number,
): string[] {
	const greedy = wrapText(ctx, text, maxWidth);
	const N = greedy.length;
	if (N <= 1) return greedy; // nothing to balance

	// Lower bound: widest single word (target width can never be narrower)
	const allWords = text.split(/[\n\s]+/).filter(Boolean);
	const minW = Math.max(...allWords.map((w) => ctx.measureText(w).width));

	// Binary search for the minimum target width that still wraps into N lines
	let lo = Math.ceil(minW);
	let hi = maxWidth;
	while (lo < hi - 1) {
		const mid = Math.floor((lo + hi) / 2);
		if (wrapText(ctx, text, mid).length <= N) hi = mid;
		else lo = mid + 1;
	}
	return wrapText(ctx, text, hi);
}

export function fitFontSize(
	ctx: { font: string; measureText(t: string): { width: number } },
	text: string,
	fontFamily: string,
	boxW: number,
	boxH: number,
	startSize: number,
	maxSize?: number,
): number {
	const maxW = Math.max(10, boxW * (1 - 2 * BOX_INSET));
	const maxH = Math.max(10, boxH * (1 - 2 * BOX_INSET));
	let lo = MIN_FONT_SIZE;
	let hi = Math.max(lo, maxSize ?? startSize);
	while (lo < hi) {
		const mid = Math.ceil((lo + hi) / 2);
		ctx.font = `${mid}px ${fontFamily}`;
		const lines = wrapText(ctx, text, maxW);
		const lineH = mid * LINE_HEIGHT;
		if (lines.length * lineH <= maxH && lines.length <= MAX_LINES) lo = mid;
		else hi = mid - 1;
	}
	return lo;
}

/**
 * Like fitFontSize but the text MUST stay on a single line (no wrapping).
 * Used for titles where wrapping would break the expected one-line layout.
 */
export function fitSingleLineSize(
	ctx: { font: string; measureText(t: string): { width: number } },
	text: string,
	fontFamily: string,
	maxW: number,
	maxH: number,
	startSize: number,
): number {
	let lo = MIN_FONT_SIZE;
	let hi = Math.max(lo, startSize);
	while (lo < hi) {
		const mid = Math.ceil((lo + hi) / 2);
		ctx.font = `${mid}px ${fontFamily}`;
		const textWidth = ctx.measureText(text).width;
		const lineH = mid * LINE_HEIGHT;
		if (textWidth <= maxW && lineH <= maxH) lo = mid;
		else hi = mid - 1;
	}
	return lo;
}

/**
 * Draw a title with canvas-drawn [ ] brackets on either side.
 * The brackets are drawn as thick open paths (two-pass: dark outline, then fill-colour inner),
 * matching the comic bold-outline style of the CC Wild Words text.
 */
export function drawBracketedTitle(
	ctx: CanvasRenderingContext2D,
	text: string,
	cx: number,
	drawY: number,
	size: number,
	fill: string,
	stroke: string,
): void {
	ctx.font = `${size}px ${FONT_DIALOGUE}`;
	ctx.textAlign = 'center';
	ctx.textBaseline = 'alphabetic';

	const textW  = ctx.measureText(text).width;
	// Bracket geometry — scaled to font size so they look heavy enough at any size
	const spineTop = drawY - size * 0.88;   // top of vertical bar (cap-height)
	const spineBot = drawY + size * 0.22;   // bottom of vertical bar (descender)
	const armLen   = size * 0.32;           // length of top/bottom horizontal arms
	const gutter   = size * 0.20;           // gap between text edge and bracket inner edge
	const lw       = Math.max(3, size * 0.15); // line weight — bold enough to match font weight

	const leftInner  = cx - textW / 2 - gutter;  // inner edge of left bracket
	const rightInner = cx + textW / 2 + gutter;  // inner edge of right bracket

	ctx.lineCap  = 'square';  // crisp right-angle corners at bracket tips
	ctx.lineJoin = 'miter';

	// Two-pass rendering: thick dark outline first, then thinner fill-colour on top
	for (let pass = 0; pass < 2; pass++) {
		ctx.strokeStyle = pass === 0 ? stroke : fill;
		ctx.lineWidth   = pass === 0 ? lw      : lw * 0.52;

		// Left bracket [ — spine on the FAR LEFT, arms extend RIGHT toward text
		ctx.beginPath();
		ctx.moveTo(leftInner,          spineTop);
		ctx.lineTo(leftInner - armLen, spineTop);
		ctx.lineTo(leftInner - armLen, spineBot);
		ctx.lineTo(leftInner,          spineBot);
		ctx.stroke();

		// Right bracket ] — spine on the FAR RIGHT, arms extend LEFT toward text
		ctx.beginPath();
		ctx.moveTo(rightInner,          spineTop);
		ctx.lineTo(rightInner + armLen, spineTop);
		ctx.lineTo(rightInner + armLen, spineBot);
		ctx.lineTo(rightInner,          spineBot);
		ctx.stroke();
	}

	// Draw the text on top (same outline+fill style as all other comic text)
	ctx.lineWidth   = Math.max(1.5, size * 0.08);
	ctx.lineJoin    = 'round';
	ctx.lineCap     = 'round';
	ctx.strokeStyle = stroke;
	ctx.strokeText(text, cx, drawY);
	ctx.fillStyle   = fill;
	ctx.fillText(text, cx, drawY);
}

// -- PAGE COMPOSITION -- //

export function renderText(r: TypesetRegion): string {
	// Stat-panel body text keeps sentence case; everything else is uppercased.
	const segs = parseStatPanel(r.text);
	if (segs) {
		// Reconstruct the multi-segment text with per-segment casing already applied inside parseStatPanel.
		// The flat string is only used by callers that don't go through typesetStatPanel.
		return segs.map((s) => s.text).join('\n');
	}
	return r.text.toUpperCase();
}

export interface TypesetOptions {
	fontScale?: number;
}

// -- STAT-PANEL RENDERER -- //

/**
 * Draw a structured stat-panel (title / rarity / subtitle / body / flavour segments)
 * stacked vertically, centre-aligned, inside the region box.
 */
export function typesetStatPanel(
	ctx: CanvasRenderingContext2D,
	r: TypesetRegion,
	segments: TextSegment[],
	bgColor: TextColor,
): void {
	const { x, y, w, h } = r.box;
	const font = fontFor(r.category);
	const insetW = Math.max(10, w * (1 - 2 * BOX_INSET));
	const insetH = Math.max(10, h * (1 - 2 * BOX_INSET));

	// -- MEASURE: binary-search a single base font size so ALL segments fit at maximum size --
	//
	// Each segment kind scales from the base by a fixed multiplier preserving visual hierarchy.
	// Binary search finds the largest base where the total stacked height <= insetH.
	// This is vastly better than sizing each segment independently and then scaling down:
	// it avoids over-shrinking and guarantees maximum use of available space.
	const SEG_SCALE: Record<SegmentKind, number> = {
		title:    segments.length === 1 ? 1.0 : 1.30,  // standalone title fills its own box
		rarity:   1.15,
		subtitle: 0.80,
		body:     1.00,
		flavour:  0.80,
	};

	const gap = Math.max(2, h * 0.012);
	const gapTotal = gap * (segments.length - 1);

	// Brackets extend outward from the text edge, so they don't reduce text width.
	// Only reserve the small gutter between text and bracket spine (0.20 each side).
	const BRACKET_GUTTER_RATIO = 0.40; // 2 × gutter (size * 0.20 each side)

	function totalAtBase(base: number): number {
		let h2 = gapTotal;
		for (const seg of segments) {
			const sz = Math.max(MIN_FONT_SIZE, Math.round(base * SEG_SCALE[seg.kind]));
			ctx.font = `${sz}px ${font}`;
			if (seg.kind === 'title') {
				// Title stays on one line; brackets extend outward so only reserve gutter space
				const maxTitleW = insetW - sz * BRACKET_GUTTER_RATIO;
				const textFits = ctx.measureText(seg.text).width <= Math.max(10, maxTitleW);
				if (!textFits) return Infinity; // base too large for title

				h2 += sz * LINE_HEIGHT;
			} else {
				const lines = balancedWrapText(ctx, seg.text, insetW);
				h2 += lines.length * sz * LINE_HEIGHT;
			}
		}
		return h2;
	}

	let lo = MIN_FONT_SIZE;
	let hi = 80; // no comic page needs body text > 80px
	while (lo < hi) {
		const mid = Math.ceil((lo + hi) / 2);
		if (totalAtBase(mid) <= insetH) lo = mid;
		else hi = mid - 1;
	}
	const baseSize = lo;

	// Build the measured array at the found baseSize
	type MeasuredSeg = { seg: TextSegment; lines: string[]; size: number; color: string; stroke: string };
	const measured: MeasuredSeg[] = [];
	let totalH = gapTotal;

	for (const seg of segments) {
		const size = Math.max(MIN_FONT_SIZE, Math.round(baseSize * SEG_SCALE[seg.kind]));
		ctx.font = `${size}px ${font}`;
		const lines = seg.kind === 'title' ? [seg.text] : balancedWrapText(ctx, seg.text, insetW);
		totalH += lines.length * size * LINE_HEIGHT;

		measured.push({ seg, lines, size, color: bgColor.fill, stroke: bgColor.stroke });
	}

	// --- Pass 2: draw --- //
	let ty = y + (h - Math.min(totalH, insetH)) / 2;

	for (let i = 0; i < measured.length; i++) {
		const { seg, lines, size, color, stroke } = measured[i];
		const lineH = size * LINE_HEIGHT;
		ctx.font = `${size}px ${font}`;
		ctx.textAlign = 'center';
		ctx.textBaseline = 'alphabetic';

		const tx = x + w / 2;
		if (seg.kind === 'title') {
			// Single line — draw with canvas-drawn [ ] brackets
			const drawY = ty + size * 0.85;
			drawBracketedTitle(ctx, lines[0], tx, drawY, size, color, stroke);
			ty += lineH;
		} else {
			for (const line of lines) {
				const drawY = ty + size * 0.85;
				ctx.lineWidth = Math.max(1.5, size * 0.08);
				ctx.lineJoin = 'round';
				ctx.strokeStyle = stroke;
				ctx.strokeText(line, tx, drawY);
				ctx.fillStyle = color;
				ctx.fillText(line, tx, drawY);
				ty += lineH;
			}
		}
		if (i < measured.length - 1) ty += gap;
	}
}

export async function typesetPage(cleanedPng: Buffer, regions: TypesetRegion[], opts: TypesetOptions = {}): Promise<Buffer> {
	registerFonts();
	const scale = opts.fontScale ?? 1;
	const img = await loadImage(cleanedPng);
	const canvas = createCanvas(img.width, img.height);
	const ctx = canvas.getContext('2d');
	ctx.drawImage(img, 0, 0);

	for (const r of regions) {
		const rawText = r.text.trim();
		if (!rawText) continue;

		const bg = sampleBackground(img, r.box.x, r.box.y, r.box.w, r.box.h);
		const color = pickTextColor(bg);

		// STAT-PANEL PATH — structured multi-segment rendering
		const statSegments = parseStatPanel(rawText);
		if (statSegments) {
			typesetStatPanel(ctx, r, statSegments, color);
			continue;
		}

		// STANDARD PATH — flat uppercase word-wrap
		const text = rawText.toUpperCase();
		const { x, y, w, h } = r.box;
		const font = fontFor(r.category);
		const startSize = Math.max(MIN_FONT_SIZE, Math.min(w, h) * (r.category === 'sfx' ? 0.6 : 0.45) * scale);

		// CAP DIALOGUE MAX FONT SIZE SO IT SITS NATURALLY INSIDE THE BUBBLE CONTOUR
		// For dialogue/mono: also cap at MAX_DIALOGUE_FONT_SIZE so an oversized bounding box
		// (e.g. a wide paragraph union box) doesn't inflate text to fill the whole region.
		const rawMax = r.category === 'sfx' ? startSize : Math.max(startSize, Math.min(h * 0.6, startSize * 1.25));
		const categoryCap = r.category === 'sfx' ? MAX_SFX_FONT_SIZE : MAX_DIALOGUE_FONT_SIZE;
		const maxSize = Math.min(rawMax, categoryCap);
		const size = fitFontSize(ctx, text, font, w, h, startSize, maxSize);
		ctx.font = `${size}px ${font}`;
		const maxW = Math.max(10, w * (1 - 2 * BOX_INSET));
		const lines = wrapText(ctx, text, maxW);
		const lineH = size * LINE_HEIGHT;
		const totalH = lines.length * lineH;
		let ty = y + (h - totalH) / 2 + size * 0.85;
		ctx.textAlign = 'center';
		ctx.textBaseline = 'alphabetic';
		for (const line of lines) {
			const tx = x + w / 2;
			ctx.lineWidth = Math.max(1.5, size * 0.08);
			ctx.lineJoin = 'round';
			ctx.strokeStyle = color.stroke;
			ctx.strokeText(line, tx, ty);
			ctx.fillStyle = color.fill;
			ctx.fillText(line, tx, ty);
			ty += lineH;
		}
	}
	return canvas.toBuffer('image/png');
}

export function sampleBackground(
	img: Image,
	x: number,
	y: number,
	w: number,
	h: number,
): { r: number; g: number; b: number } {
	const sx = Math.max(0, Math.floor(x + w * 0.2));
	const sy = Math.max(0, Math.floor(y + h * 0.2));
	const ex = Math.min(img.width, Math.ceil(x + w * 0.8));
	const ey = Math.min(img.height, Math.ceil(y + h * 0.8));
	if (ex - sx < 4 || ey - sy < 4) return { r: 255, g: 255, b: 255 };

	const probe = createCanvas(ex - sx, ey - sy);
	const pctx = probe.getContext('2d');
	pctx.drawImage(img, sx, sy, ex - sx, ey - sy, 0, 0, ex - sx, ey - sy);
	const data = pctx.getImageData(0, 0, ex - sx, ey - sy).data;
	let r = 0;
	let g = 0;
	let b = 0;
	const n = (ex - sx) * (ey - sy);
	for (let i = 0; i < data.length; i += 4) {
		r += data[i];
		g += data[i + 1];
		b += data[i + 2];
	}
	return { r: Math.round(r / n), g: Math.round(g / n), b: Math.round(b / n) };
}
