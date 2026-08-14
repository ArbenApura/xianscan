// BOOKS API — LIST + CREATE (SINGLE-USER APP, NO AUTH).
// IMPORTED DEP-MODULES
import { error, json } from '@sveltejs/kit';
import { randomUUID } from 'node:crypto';
import { desc } from 'drizzle-orm';
import { z } from 'zod';
// IMPORTED MODULES
import { DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG } from '$lib/languages';
import { db } from '$lib/server/db';
import { books, chapters, pages } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

const PostBody = z.object({
	title: z.string().min(1).max(200),
	titleTarget: z.string().max(200).optional(),
	sourceLang: z.string().optional(),
	targetLang: z.string().optional(),
});

export const GET: RequestHandler = async () => {
	const rows = db.select().from(books).orderBy(desc(books.pinned), desc(books.updatedAt)).all();

	// FETCH ALL CHAPTERS & PAGES IN BULK TO COMPUTE RICH TELEMETRY & COVER ARTWORK
	const allChapters = db.select().from(chapters).orderBy(chapters.bookId, chapters.seq).all();
	const allPages = db
		.select({
			id: pages.id,
			chapterId: pages.chapterId,
			seq: pages.seq,
			status: pages.status,
			outputPath: pages.outputPath,
		})
		.from(pages)
		.orderBy(pages.chapterId, pages.seq)
		.all();

	const chaptersByBook = new Map<string, typeof allChapters>();
	for (const ch of allChapters) {
		const list = chaptersByBook.get(ch.bookId) ?? [];
		list.push(ch);
		chaptersByBook.set(ch.bookId, list);
	}

	const pagesByChapter = new Map<number, typeof allPages>();
	for (const pg of allPages) {
		const list = pagesByChapter.get(pg.chapterId) ?? [];
		list.push(pg);
		pagesByChapter.set(pg.chapterId, list);
	}

	return json({
		books: rows.map((b) => {
			const bookChapters = chaptersByBook.get(b.id) ?? [];
			const chapterCount = bookChapters.length;

			let pageCount = 0;
			let translatedPageCount = 0;
			let translatedChapterCount = 0;

			for (const c of bookChapters) {
				const chPages = pagesByChapter.get(c.id) ?? [];
				const chTotal = chPages.length;
				const chDone = chPages.filter((p) => p.status === 'done' || Boolean(p.outputPath)).length;

				pageCount += chTotal;
				translatedPageCount += chDone;

				const isChapterDone = c.status === 'done' || (chTotal > 0 && chDone === chTotal);
				if (isChapterDone) {
					translatedChapterCount++;
				}
			}

			// COVER THUMBNAIL: FIRST PAGE FROM EARLIEST CHAPTER WITH PAGES
			let coverPage: (typeof allPages)[0] | null = null;
			for (const c of bookChapters) {
				const pgs = pagesByChapter.get(c.id) ?? [];
				if (pgs.length > 0) {
					coverPage = pgs[0];
					break;
				}
			}

			// LATEST CHAPTER (FOR QUICK "CONTINUE READING")
			const lastChapter = bookChapters[bookChapters.length - 1] ?? null;

			return {
				id: b.id,
				title: b.title,
				titleTarget: b.titleTarget,
				sourceLang: b.sourceLang,
				targetLang: b.targetLang,
				pinned: b.pinned,
				archived: b.archived,
				createdAt: b.createdAt,
				updatedAt: b.updatedAt,
				chapterCount,
				translatedChapterCount,
				pageCount,
				translatedPageCount,
				coverPageId: coverPage?.id ?? null,
				coverHasOutput: !!coverPage?.outputPath,
				latestChapter: lastChapter
					? {
							id: lastChapter.id,
							seq: lastChapter.seq,
							title: lastChapter.title,
							titleTarget: lastChapter.titleTarget,
							status: lastChapter.status,
						}
					: null,
			};
		}),
	});
};

export const POST: RequestHandler = async ({ request }) => {
	const parsed = PostBody.safeParse(await request.json().catch(() => null));
	if (!parsed.success) throw error(400, 'Invalid book.');
	const sourceLang = parsed.data.sourceLang || DEFAULT_SOURCE_LANG;
	const targetLang = parsed.data.targetLang || DEFAULT_TARGET_LANG;

	if (sourceLang === targetLang) {
		throw error(400, 'Target translation language must be different from source language.');
	}

	const id = randomUUID();
	db.insert(books)
		.values({
			id,
			title: parsed.data.title.trim(),
			titleTarget: parsed.data.titleTarget ? parsed.data.titleTarget.trim() : null,
			sourceLang,
			targetLang,
		})
		.run();
	return json({ id }, { status: 201 });
};
