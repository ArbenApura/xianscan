// BOOK DETAIL — THE BOOK + ITS CHAPTERS (WITH PAGE COUNTS).
// IMPORTED DEP-MODULES
import { json } from '@sveltejs/kit';
import { eq, sql } from 'drizzle-orm';
// IMPORTED MODULES
import { assertBookExists } from '$lib/server/books';
import { db } from '$lib/server/db';
import { books, chapters, pages } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	await assertBookExists(params.id);
	const book = db.select().from(books).where(eq(books.id, params.id)).get();
	const list = db
		.select({
			id: chapters.id,
			title: chapters.title,
			seq: chapters.seq,
			status: chapters.status,
		})
		.from(chapters)
		.where(eq(chapters.bookId, params.id))
		.orderBy(chapters.seq)
		.all();

	// PAGE COUNTS IN ONE GROUPED QUERY, MERGED BY CHAPTER ID (NO SUBQUERY HACKS — THE LOCAL DB IS SMALL)
	const counts = db
		.select({ chapterId: pages.chapterId, n: sql<number>`count(*)` })
		.from(pages)
		.groupBy(pages.chapterId)
		.all();
	const byChapter = new Map(counts.map((c) => [c.chapterId, c.n]));

	return json({
		book,
		chapters: list.map((c) => ({ ...c, pageCount: byChapter.get(c.id) ?? 0 })),
	});
};

export const DELETE: RequestHandler = async ({ params }) => {
	await assertBookExists(params.id);
	db.delete(books).where(eq(books.id, params.id)).run();
	return json({ ok: true });
};

