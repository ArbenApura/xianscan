// TYPESET TESTS — WRAPPING, AUTOFIT, CONTRAST, AND A REAL PAGE COMPOSITION (SKIA, NO DOM NEEDED).
import { describe, expect, it } from 'vitest';
import { createCanvas, loadImage } from '@napi-rs/canvas';
import {
	decollideRegions,
	fontFor,
	fitFontSize,
	pickTextColor,
	reflowText,
	renderText,
	sampleBackground,
	typesetPage,
	wrapText,
} from '$lib/server/typeset';

function ctx() {
	const c = createCanvas(10, 10);
	const x = c.getContext('2d');
	x.font = '20px Arial';
	return x;
}

// -- COLOR / CONTRAST -- //

describe('pickTextColor', () => {
	it('white text on dark backgrounds', () => {
		expect(pickTextColor({ r: 10, g: 10, b: 10 })).toEqual({ fill: 'white', stroke: 'black' });
	});

	it('black text on light backgrounds', () => {
		expect(pickTextColor({ r: 245, g: 245, b: 245 })).toEqual({ fill: 'black', stroke: 'white' });
	});
});

describe('fontFor', () => {
	it('uses one uniform font for every category', () => {
		expect(fontFor('sfx')).toBe('CC Wild Words');
		expect(fontFor('dialogue')).toBe('CC Wild Words');
		expect(fontFor('mono')).toBe('CC Wild Words');
	});
});

// -- RENDER STRING -- //

describe('renderText', () => {
	it('uppercases Latin text at render time', () => {
		expect(renderText({ id: 'r0', box: { x: 0, y: 0, w: 10, h: 10 }, text: 'Watch out! He\'s attacking again!', category: 'dialogue' })).toBe(
			'WATCH OUT! HE\'S ATTACKING AGAIN!',
		);
	});

	it('uppercases accented letters', () => {
		expect(renderText({ id: 'r0', box: { x: 0, y: 0, w: 10, h: 10 }, text: 'héllo wörld', category: 'dialogue' })).toBe('HÉLLO WÖRLD');
	});

	it('leaves CJK and punctuation unchanged (preserves unicode symbols for font fallback rendering)', () => {
		expect(renderText({ id: 'r0', box: { x: 0, y: 0, w: 10, h: 10 }, text: '小心！BOOM…', category: 'dialogue' })).toBe('小心！BOOM…');
	});
});

// -- LAYOUT -- //

describe('wrapText', () => {
	it('keeps short text on one line', () => {
		expect(wrapText(ctx(), 'Hello', 500)).toEqual(['Hello']);
	});

	it('wraps at word boundaries', () => {
		const lines = wrapText(ctx(), 'the quick brown fox jumps over the lazy dog', 140);
		expect(lines.length).toBeGreaterThan(1);
		// EVERY LINE (EXCEPT THE LAST) MUST FIT THE WIDTH
		for (const line of lines.slice(0, -1)) {
			expect(ctx().measureText(line).width).toBeLessThanOrEqual(140);
		}
		expect(lines.join(' ')).toBe('the quick brown fox jumps over the lazy dog');
	});

	it('character-breaks a single over-long word instead of dropping it', () => {
		const lines = wrapText(ctx(), 'supercalifragilisticexpialidocious', 60);
		expect(lines.length).toBeGreaterThan(1);
		expect(lines.join('')).toBe('supercalifragilisticexpialidocious');
	});

	it('caps the line count', () => {
		const long = Array.from({ length: 30 }, (_, i) => `word${i}`).join(' ');
		expect(wrapText(ctx(), long, 30).length).toBeLessThanOrEqual(8);
	});

	it('treats \\n as a hard line break (multi-line bubble paragraphs)', () => {
		const lines = wrapText(ctx(), 'Hello there.\nSecond line here.', 1000);
		expect(lines).toEqual(['Hello there.', 'Second line here.']);
	});

	it('never strands a lone trailing punctuation on its own line', () => {
		// THE USER-REPORTED FAILURE: "TRANSMIGRATION..." OVERFLOWS, SO CHARACTER-BREAKING
		// DROPPED THE FINAL "." ONTO THE NEXT LINE. THE DOT MUST STAY WITH THE WORD.
		const measure = { measureText: (t: string) => ({ width: t.length * 10 }) };
		const lines = wrapText(measure, 'TRANSMIGRATION...', 160);
		expect(lines.length).toBe(1);
		expect(lines[0]).toBe('TRANSMIGRATION...');
	});

	it('re-attaches a space-separated trailing dot that overflows', () => {
		const measure = { measureText: (t: string) => ({ width: t.length * 10 }) };
		const lines = wrapText(measure, 'TRANSMIGRATION.. .', 160);
		expect(lines.join('')).toBe('TRANSMIGRATION...'); // THE DOT ATTACHED WITHOUT A SPACE
		expect(lines.some((l) => /^[.．…·!！?？]+$/.test(l))).toBe(false);
	});
});

describe('reflowText', () => {
	it('merges OCR line breaks into one balanced paragraph', () => {
		const c = ctx();
		const text = 'Bored out of her mind,\nXin\nawakened or perhaps\nenlightened\nthis sliver of humanity.';
		const lines = reflowText(c, text, 300);
		// THE FIVE OCR LINES COLLAPSE — NO ORPHANED SINGLE WORD
		expect(lines.length).toBeLessThan(5);
		expect(lines.includes('Xin')).toBe(false);
		expect(lines.join(' ')).toBe(
			'Bored out of her mind, Xin awakened or perhaps enlightened this sliver of humanity.',
		);
	});

	it('prioritizes the upper lines over the last line', () => {
		const c = ctx();
		const text = 'Bored out of her mind, Xin awakened or perhaps enlightened this sliver of humanity.';
		const lines = reflowText(c, text, 300);
		expect(lines.length).toBeGreaterThan(1);
		const widths = lines.map((l) => c.measureText(l).width);
		// THE FIRST LINE IS THE FULLEST; THE LAST LINE IS THE SHORTEST
		expect(widths[0]).toBeGreaterThanOrEqual(widths[widths.length - 1]);
		for (const w of widths) expect(w).toBeLessThanOrEqual(300);
	});
});

describe('fitFontSize', () => {
	it('finds a size whose layout fits the box', () => {
		const c = createCanvas(10, 10);
		const x = c.getContext('2d');
		const size = fitFontSize(x, 'Hello world this is dialogue', 'Arial', 200, 100, 60);
		expect(size).toBeGreaterThanOrEqual(8);
		expect(size).toBeLessThanOrEqual(60);

		// VERIFY THE FOUND SIZE ACTUALLY FITS (UNDER THE CURRENT CONSTANTS: INSET 0.05, PITCH 1.2)
		x.font = `${size}px Arial`;
		const lines = wrapText(x, 'Hello world this is dialogue', 200 * 0.9);
		expect(lines.length * size * 1.2).toBeLessThanOrEqual(100 * 0.9);
	});

	it('degrades gracefully for tiny boxes', () => {
		const c = createCanvas(10, 10);
		const x = c.getContext('2d');
		const size = fitFontSize(x, 'A very long sentence here', 'Arial', 40, 20, 40);
		expect(size).toBeGreaterThanOrEqual(8); // THE FLOOR, NOT A CRASH
	});

	it('scales a short line up to fill a tall box when maxSize is given (dialogue)', () => {
		const c = createCanvas(10, 10);
		const x = c.getContext('2d');
		// 'HI!' IN A WIDE TALL BOX — THE OLD min(w,h)*0.5 CEILING (25) LEFT THE BUBBLE ~60% EMPTY.
		// WITH maxSize THE HEIGHT BUDGET IS THE ONLY LIMIT: 0.9×100 / 1.2 ≈ 75.
		const size = fitFontSize(x, 'HI!', 'Arial', 200, 100, 25, 90);
		expect(size).toBeGreaterThan(60);
	});

	it('keeps the startSize ceiling when maxSize is omitted (sfx exemption)', () => {
		const c = createCanvas(10, 10);
		const x = c.getContext('2d');
		// SFX NEVER PASSES maxSize — IMPACT TEXT KEEPS ITS INTENTIONAL WHITESPACE.
		const size = fitFontSize(x, 'HI!', 'Arial', 200, 100, 25);
		expect(size).toBe(25);
	});
});

// -- PAGE COMPOSITION -- //

describe('typesetPage', () => {
	function blankPng(w: number, h: number, color: string): Buffer {
		const c = createCanvas(w, h);
		const x = c.getContext('2d');
		x.fillStyle = color;
		x.fillRect(0, 0, w, h);
		return c.toBuffer('image/png');
	}

	async function brightPixels(png: Buffer): Promise<number> {
		const img = await loadImage(png);
		const probe = createCanvas(img.width, img.height);
		const px = probe.getContext('2d');
		px.drawImage(img, 0, 0);
		const data = px.getImageData(0, 0, img.width, img.height).data;
		let bright = 0;
		for (let i = 0; i < data.length; i += 4) {
			if (data[i] > 200) bright++;
		}
		return bright;
	}

	it('renders text over a dark page with a light fill', async () => {
		const out = await typesetPage(blankPng(400, 300, 'black'), [
			{ id: 'r0', box: { x: 50, y: 100, w: 300, h: 80 }, text: 'Hello world', category: 'dialogue' },
		]);
		expect(out.slice(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])); // PNG MAGIC
		expect(await brightPixels(out)).toBeGreaterThan(0); // WHITE TEXT ON THE BLACK PAGE
	});

	it('uses dark text on a light page (contrast flips with the background)', async () => {
		const out = await typesetPage(blankPng(400, 300, 'white'), [
			{ id: 'r0', box: { x: 50, y: 100, w: 300, h: 80 }, text: 'Hello world', category: 'dialogue' },
		]);
		const img = await loadImage(out);
		const probe = createCanvas(img.width, img.height);
		const px = probe.getContext('2d');
		px.drawImage(img, 0, 0);
		const data = px.getImageData(0, 0, img.width, img.height).data;
		let dark = 0;
		for (let i = 0; i < data.length; i += 4) {
			if (data[i] < 60) dark++;
		}
		expect(dark).toBeGreaterThan(0); // BLACK TEXT ON THE WHITE PAGE
	});

	it('skips empty-text regions', async () => {
		const out = await typesetPage(blankPng(100, 100, 'white'), [
			{ id: 'r0', box: { x: 10, y: 10, w: 80, h: 40 }, text: '   ', category: 'dialogue' },
		]);
		expect(out.length).toBeGreaterThan(0); // STILL A VALID PNG, NOTHING CRASHED
	});

	it('renders an sfx region without crashing', async () => {
		const out = await typesetPage(blankPng(300, 200, 'white'), [
			{ id: 'r0', box: { x: 10, y: 10, w: 280, h: 100 }, text: 'BOOM!', category: 'sfx' },
		]);
		expect(out.slice(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
	});
});

describe('sampleBackground', () => {
	it('reads the actual page color (regression: the sync decode raced and sampled black)', async () => {
		const c = createCanvas(100, 100);
		const x = c.getContext('2d');
		x.fillStyle = 'white';
		x.fillRect(0, 0, 100, 100);
		const img = await loadImage(c.toBuffer('image/png'));
		expect(sampleBackground(img, 10, 10, 80, 80)).toEqual({ r: 255, g: 255, b: 255 });
	});
});

describe('decollideRegions', () => {
	it('adjusts overlapping bounding boxes to eliminate collision gap', () => {
		const r1 = { id: 'r0', box: { x: 50, y: 10, w: 100, h: 50 }, text: 'Daddy!', category: 'dialogue' as const };
		const r2 = { id: 'r1', box: { x: 50, y: 40, w: 100, h: 50 }, text: 'Wake up!', category: 'dialogue' as const };

		const result = decollideRegions([r1, r2]);
		expect(result).toHaveLength(2);
		// Bottom box y should shift downwards to eliminate overlap
		expect(result[1].box.y).toBeGreaterThan(r2.box.y);
	});
});
