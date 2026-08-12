// SERVE A PAGE'S IMAGE (original | cleaned | output) AS BYTES.
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { eq } from 'drizzle-orm';
import { readFileSync } from 'node:fs';
import { extname, join } from 'node:path';
// IMPORTED MODULES
import { db } from '$lib/server/db';
import { pages } from '$lib/server/db/schema';
import { DATA_ROOT } from '$lib/server/paths';
import type { RequestHandler } from './$types';

const KINDS = new Set(['original', 'cleaned', 'output']);

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
	if (!KINDS.has(kind)) throw error(400, 'kind must be original | cleaned | output.');

	const page = db.select().from(pages).where(eq(pages.id, pageId)).get();
	if (!page) throw error(404, 'Page not found.');

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
