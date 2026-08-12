// CHAPTER DETAIL — PAGES (WITH THEIR REGIONS) FOR THE RESULTS VIEW.
// IMPORTED DEP-MODULES
import { error, json } from '@sveltejs/kit';
import { eq, inArray } from 'drizzle-orm';
// IMPORTED MODULES
import { assertChapterExists } from '$lib/server/chapters';
import { db } from '$lib/server/db';
import { pages, regions, chapters } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	const chapterId = Number(params.id);
	if (!Number.isInteger(chapterId)) throw error(400, 'Invalid chapter id.');
	await assertChapterExists(chapterId);

	const pageRows = db
		.select()
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(pages.seq)
		.all();

	// ALL REGIONS FOR THESE PAGES IN ONE QUERY, GROUPED BY PAGE
	const pageIds = pageRows.map((p) => p.id);
	const regionRows =
		pageIds.length > 0
			? db
					.select()
					.from(regions)
					.where(inArray(regions.pageId, pageIds))
					.orderBy(regions.seq)
					.all()
			: [];
	const byPage = new Map<number, typeof regionRows>();
	for (const r of regionRows) {
		const list = byPage.get(r.pageId) ?? [];
		list.push(r);
		byPage.set(r.pageId, list);
	}

	return json({
		pages: pageRows.map((p) => ({
			id: p.id,
			seq: p.seq,
			filePath: p.filePath,
			cleanedPath: p.cleanedPath,
			outputPath: p.outputPath,
			status: p.status,
			error: p.error,
			width: p.width,
			height: p.height,
			regions: (byPage.get(p.id) ?? []).map((r) => ({
				id: r.id,
				seq: r.seq,
				box: safeJson(r.box),
				category: r.category,
				textSource: r.textSource,
				textTarget: r.textTarget,
				conf: r.conf,
			})),
		})),
	});
};

export const DELETE: RequestHandler = async ({ params }) => {
	const chapterId = Number(params.id);
	if (!Number.isInteger(chapterId)) throw error(400, 'Invalid chapter id.');
	await assertChapterExists(chapterId);
	db.delete(chapters).where(eq(chapters.id, chapterId)).run();
	return json({ ok: true });
};

function safeJson(raw: string): unknown {

	try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
}
