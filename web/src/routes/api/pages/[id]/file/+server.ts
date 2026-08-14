// SERVE A PAGE'S IMAGE (original | cleaned | output | thumb) AS BYTES.
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { eq } from 'drizzle-orm';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { extname, join } from 'node:path';
import { createCanvas, loadImage } from '@napi-rs/canvas';
// IMPORTED MODULES
import { db } from '$lib/server/db';
import { pages } from '$lib/server/db/schema';
import { DATA_ROOT } from '$lib/server/paths';
import type { RequestHandler } from './$types';

const KINDS = new Set(['original', 'cleaned', 'output', 'thumb']);

// CONTENT TYPE BY EXTENSION — ORIGINALS CAN BE PNG/JPEG/WEBP/AVIF, NOT ALWAYS PNG.
const MIME_BY_EXT: Record<string, string> = {
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.webp': 'image/webp',
	'.avif': 'image/avif',
};

export const GET: RequestHandler = async ({ params, url }) => {
	const pageId = Number(params.id);
	if (!Number.isInteger(pageId)) throw error(400, 'Invalid page id.');
	const kind = url.searchParams.get('kind') ?? 'original';
	if (!KINDS.has(kind)) throw error(400, 'kind must be original | cleaned | output | thumb.');

	const page = db.select().from(pages).where(eq(pages.id, pageId)).get();
	if (!page) throw error(404, 'Page not found.');

	// THUMBNAIL SERVING & MEMOIZED DISK CACHING
	if (kind === 'thumb') {
		const targetWidth = Math.min(800, Math.max(80, parseInt(url.searchParams.get('w') || '280', 10)));
		const rel = page.outputPath ?? page.filePath;
		if (!rel) throw error(404, 'No image available for this page.');

		const thumbDir = join(DATA_ROOT, 'cache', 'thumbs');
		const cacheKey = `${page.id}_${page.outputPath ? 'out' : 'orig'}_${targetWidth}.jpg`;
		const cachePath = join(thumbDir, cacheKey);

		if (existsSync(cachePath)) {
			const cachedBytes = readFileSync(cachePath);
			return new Response(cachedBytes, {
				headers: {
					'content-type': 'image/jpeg',
					'cache-control': 'public, max-age=604800, stale-while-revalidate=86400',
				},
			});
		}

		try {
			mkdirSync(thumbDir, { recursive: true });
			const img = await loadImage(join(DATA_ROOT, rel));
			const scale = targetWidth / img.width;
			const targetHeight = Math.round(img.height * scale);

			const canvas = createCanvas(targetWidth, targetHeight);
			const ctx = canvas.getContext('2d');
			ctx.drawImage(img, 0, 0, targetWidth, targetHeight);
			const jpegBuffer = canvas.toBuffer('image/jpeg', 80);

			writeFileSync(cachePath, jpegBuffer);

			return new Response(new Uint8Array(jpegBuffer), {
				headers: {
					'content-type': 'image/jpeg',
					'cache-control': 'public, max-age=604800, stale-while-revalidate=86400',
				},
			});
		} catch {
			// FALLBACK TO FULL IMAGE IF THUMBNAIL RESIZING ENCOUNTERS AN UNEXPECTED IO ISSUE
			const bytes = readFileSync(join(DATA_ROOT, rel));
			return new Response(bytes, {
				headers: { 'content-type': MIME_BY_EXT[extname(rel).toLowerCase()] ?? 'image/jpeg' },
			});
		}
	}

	const rel =
		kind === 'cleaned'
			? page.cleanedPath
			: kind === 'output'
				? page.outputPath
				: page.filePath;
	if (!rel) throw error(404, `No ${kind} image for this page yet.`);
	const bytes = readFileSync(join(DATA_ROOT, rel));
	const mime = MIME_BY_EXT[extname(rel).toLowerCase()] ?? 'application/octet-stream';

	return new Response(bytes, {
		headers: { 'content-type': mime, 'cache-control': 'no-cache' },
	});
};
