// CHAPTER DETAIL — PAGES (WITH THEIR REGIONS) FOR THE RESULTS VIEW & EDIT / DELETE.
// IMPORTED DEP-MODULES
import { error, json } from '@sveltejs/kit';
import { eq, inArray } from 'drizzle-orm';
import { z } from 'zod';
// IMPORTED MODULES
import { assertChapterExists } from '$lib/server/chapters';
import { db } from '$lib/server/db';
import { pages, regions, chapters, books } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

const PatchChapterBody = z.object({
	title: z.string().max(200).optional(),
	titleTarget: z.string().max(200).nullable().optional(),
	seq: z.number().int().min(0).optional(),
});

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
		const arr = byPage.get(r.pageId) ?? [];
		arr.push(r);
		byPage.set(r.pageId, arr);
	}

	const chapterRow = db
		.select()
		.from(chapters)
		.where(eq(chapters.id, chapterId))
		.get();

	const bookRow = chapterRow
		? db.select().from(books).where(eq(books.id, chapterRow.bookId)).get()
		: null;

	const allChaptersInBook = chapterRow
		? db
				.select({
					id: chapters.id,
					seq: chapters.seq,
					title: chapters.title,
					titleTarget: chapters.titleTarget,
				})
				.from(chapters)
				.where(eq(chapters.bookId, chapterRow.bookId))
				.orderBy(chapters.seq)
				.all()
		: [];
	const currentIndex = allChaptersInBook.findIndex((c) => c.id === chapterId);
	const prevChapter = currentIndex > 0 ? allChaptersInBook[currentIndex - 1] : null;
	const nextChapter =
		currentIndex >= 0 && currentIndex < allChaptersInBook.length - 1
			? allChaptersInBook[currentIndex + 1]
			: null;

	return json({
		chapter: chapterRow
			? {
					id: chapterRow.id,
					bookId: chapterRow.bookId,
					seq: chapterRow.seq,
					title: chapterRow.title,
					titleTarget: chapterRow.titleTarget,
					sourceLang: bookRow?.sourceLang || 'zh-CN',
					targetLang: bookRow?.targetLang || 'en',
				}
			: null,
		allChapters: allChaptersInBook,
		prevChapter,
		nextChapter,
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

export const PATCH: RequestHandler = async ({ params, request }) => {
	const chapterId = Number(params.id);
	if (!Number.isInteger(chapterId)) throw error(400, 'Invalid chapter id.');
	await assertChapterExists(chapterId);

	const parsed = PatchChapterBody.safeParse(await request.json().catch(() => null));
	if (!parsed.success) throw error(400, 'Invalid update data.');

	const updates: Record<string, unknown> = {};
	if (parsed.data.title !== undefined) updates.title = parsed.data.title.trim();
	if (parsed.data.titleTarget !== undefined) updates.titleTarget = parsed.data.titleTarget ? parsed.data.titleTarget.trim() : null;
	if (parsed.data.seq !== undefined) updates.seq = parsed.data.seq;

	if (Object.keys(updates).length > 0) {
		db.update(chapters).set(updates).where(eq(chapters.id, chapterId)).run();
	}

	const updated = db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
	return json({ chapter: updated });
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
