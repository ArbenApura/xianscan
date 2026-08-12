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

// -- PAGE COMPOSITION -- //

export function renderText(r: TypesetRegion): string {
	return r.text.toUpperCase();
}

export interface TypesetOptions {
	fontScale?: number;
}

export async function typesetPage(cleanedPng: Buffer, regions: TypesetRegion[], opts: TypesetOptions = {}): Promise<Buffer> {
	registerFonts();
	const scale = opts.fontScale ?? 1;
	const img = await loadImage(cleanedPng);
	const canvas = createCanvas(img.width, img.height);
	const ctx = canvas.getContext('2d');
	ctx.drawImage(img, 0, 0);

	for (const r of regions) {
		const text = renderText(r).trim();
		if (!text) continue;
		const { x, y, w, h } = r.box;
		const font = fontFor(r.category);
		const startSize = Math.max(MIN_FONT_SIZE, Math.min(w, h) * (r.category === 'sfx' ? 0.6 : 0.45) * scale);

		const bg = sampleBackground(img, x, y, w, h);
		const color = pickTextColor(bg);

		// CAP DIALOGUE MAX FONT SIZE SO IT SITS NATURALLY INSIDE THE BUBBLE CONTOUR
		const maxSize = r.category === 'sfx' ? startSize : Math.max(startSize, Math.min(h * 0.6, startSize * 1.25));
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
