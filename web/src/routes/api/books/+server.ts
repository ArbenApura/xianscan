// BOOKS API — LIST + CREATE (SINGLE-USER APP, NO AUTH).
// IMPORTED DEP-MODULES
import { error, json } from '@sveltejs/kit';
import { randomUUID } from 'node:crypto';
import { sql } from 'drizzle-orm';
import { z } from 'zod';
// IMPORTED MODULES
import { DEFAULT_SOURCE_LANG, DEFAULT_TARGET_LANG } from '$lib/languages';
import { db } from '$lib/server/db';
import { books, chapters } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

const PostBody = z.object({
	title: z.string().min(1).max(200),
	sourceLang: z.string().optional(),
	targetLang: z.string().optional(),
});

export const GET: RequestHandler = async () => {
	const rows = db.select().from(books).orderBy(books.createdAt).all();
	// CHAPTER COUNTS IN ONE GROUPED QUERY, THEN MERGED (DRIZZLE SQLITE HAS NO SUBQUERY SELECTOR)
	const counts = db
		.select({ bookId: chapters.bookId, n: sqlCount() })
		.from(chapters)
		.groupBy(chapters.bookId)
		.all();
	const byBook = new Map(counts.map((c) => [c.bookId, c.n]));
	return json({
		books: rows.map((b) => ({
			id: b.id,
			title: b.title,
			sourceLang: b.sourceLang,
			targetLang: b.targetLang,
			chapterCount: byBook.get(b.id) ?? 0,
		})),
	});
};

export const POST: RequestHandler = async ({ request }) => {
	const parsed = PostBody.safeParse(await request.json().catch(() => null));
	if (!parsed.success) throw error(400, 'Invalid book.');
	const id = randomUUID();
	db.insert(books)
		.values({
			id,
			title: parsed.data.title,
			sourceLang: parsed.data.sourceLang || DEFAULT_SOURCE_LANG,
			targetLang: parsed.data.targetLang || DEFAULT_TARGET_LANG,
		})
		.run();
	return json({ id }, { status: 201 });
};

// SQLite count(*) VIA A RAW SQL EXPRESSION (DRIZZLE'S sqlite-core count() NEEDS A COLUMN).
function sqlCount() {
	return sql<number>`count(*)`;
}
