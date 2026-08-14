// BOOK DETAIL — THE BOOK + ITS CHAPTERS (WITH PAGE COUNTS, THUMBNAILS, & TELEMETRY) & EDIT / DELETE.
// IMPORTED DEP-MODULES
import { error, json } from '@sveltejs/kit';
import { eq, inArray } from 'drizzle-orm';
import { z } from 'zod';
// IMPORTED MODULES
import { assertBookExists } from '$lib/server/books';
import { db } from '$lib/server/db';
import { books, chapters, pages } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

const PatchBody = z.object({
	title: z.string().min(1).max(200).optional(),
	titleTarget: z.string().max(200).nullable().optional(),
	sourceLang: z.string().optional(),
	targetLang: z.string().optional(),
	pinned: z.boolean().optional(),
	archived: z.boolean().optional(),
});

export const GET: RequestHandler = async ({ params }) => {
	await assertBookExists(params.id);
	const book = db.select().from(books).where(eq(books.id, params.id)).get();
	const list = db
		.select({
			id: chapters.id,
			title: chapters.title,
			titleTarget: chapters.titleTarget,
			seq: chapters.seq,
			status: chapters.status,
			translatedAt: chapters.translatedAt,
			createdAt: chapters.createdAt,
		})
		.from(chapters)
		.where(eq(chapters.bookId, params.id))
		.orderBy(chapters.seq)
		.all();

	const chapterIds = list.map((c) => c.id);
	const chapterPages =
		chapterIds.length > 0
			? db
					.select({
						id: pages.id,
						chapterId: pages.chapterId,
						seq: pages.seq,
						status: pages.status,
						outputPath: pages.outputPath,
					})
					.from(pages)
					.where(inArray(pages.chapterId, chapterIds))
					.orderBy(pages.chapterId, pages.seq)
					.all()
			: [];

	const pagesByChapter = new Map<number, typeof chapterPages>();
	for (const p of chapterPages) {
		const arr = pagesByChapter.get(p.chapterId) ?? [];
		arr.push(p);
		pagesByChapter.set(p.chapterId, arr);
	}

	return json({
		book,
		chapters: list.map((c) => {
			const pgs = pagesByChapter.get(c.id) ?? [];
			const pageCount = pgs.length;
			const translatedPageCount = pgs.filter((p) => p.status === 'done' || Boolean(p.outputPath)).length;
			const firstPage = pgs[0] ?? null;
			const isDone = c.status === 'done' || (pageCount > 0 && translatedPageCount === pageCount);
			return {
				...c,
				status: isDone ? 'done' : c.status,
				pageCount,
				translatedPageCount,
				coverPageId: firstPage?.id ?? null,
				coverHasOutput: !!firstPage?.outputPath,
			};
		}),
	});
};

export const PATCH: RequestHandler = async ({ params, request }) => {
	await assertBookExists(params.id);
	const parsed = PatchBody.safeParse(await request.json().catch(() => null));
	if (!parsed.success) throw error(400, 'Invalid update data.');

	const updates: Record<string, unknown> = {
		updatedAt: Date.now(),
	};
	if (parsed.data.title !== undefined) updates.title = parsed.data.title.trim();
	if (parsed.data.titleTarget !== undefined) updates.titleTarget = parsed.data.titleTarget ? parsed.data.titleTarget.trim() : null;
	if (parsed.data.sourceLang !== undefined) updates.sourceLang = parsed.data.sourceLang;
	if (parsed.data.targetLang !== undefined) updates.targetLang = parsed.data.targetLang;
	if (parsed.data.pinned !== undefined) updates.pinned = parsed.data.pinned;
	if (parsed.data.archived !== undefined) updates.archived = parsed.data.archived;

	db.update(books).set(updates).where(eq(books.id, params.id)).run();

	const updated = db.select().from(books).where(eq(books.id, params.id)).get();
	return json({ book: updated });
};

export const DELETE: RequestHandler = async ({ params }) => {
	await assertBookExists(params.id);
	db.delete(books).where(eq(books.id, params.id)).run();
	return json({ ok: true });
};
