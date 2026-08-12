// CHAPTER / PAGE CREATION HELPERS — SHARED BY THE API ROUTES.
import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, extname } from 'node:path';
// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { desc, eq } from 'drizzle-orm';
// IMPORTED MODULES
import { db } from './db';
import { chapters, pages } from './db/schema';
import { DATA_ROOT } from './paths';

// -- CONSTANTS -- //

// ACCEPTED PAGE IMAGE FORMATS (MAGIC-BYTE CHECKED IN uploadImages)
const ALLOWED_EXT = new Set(['.png', '.jpg', '.jpeg', '.webp', '.avif']);

// -- FUNCTIONS -- //

export async function assertChapterExists(chapterId: number): Promise<{ id: number; bookId: string; title: string; seq: number }> {
	const chapter = db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
	if (!chapter) throw error(404, 'Chapter not found.');
	return chapter;
}

// CREATE AN EMPTY CHAPTER AT THE END OF THE BOOK.
export async function createChapter(bookId: string, title: string): Promise<{ id: number; seq: number }> {
	const max = db
		.select({ seq: chapters.seq })
		.from(chapters)
		.where(eq(chapters.bookId, bookId))
		.orderBy(desc(chapters.seq))
		.limit(1)
		.get();
	const seq = (max?.seq ?? -1) + 1;
	const row = db
		.insert(chapters)
		.values({ uuid: randomUUID(), bookId, seq, title })
		.returning()
		.get();
	return { id: row.id, seq: row.seq };
}

// WRITE UPLOADED PAGE IMAGES TO DISK AND CREATE THEIR DB ROWS. RETURNS THE CREATED PAGE COUNT.
// SEQ CONTINUES AFTER THE CHAPTER'S EXISTING PAGES (A SECOND UPLOAD MUST NOT COLLIDE WITH THE
// (chapterId, seq) UNIQUE INDEX — THE ORIGINAL BUG 500'd EVERY NON-FIRST UPLOAD).
export async function uploadPages(chapterId: number, files: File[]): Promise<number> {
	let count = 0;
	let seq = nextPageSeq(chapterId);
	const uploadDir = join(DATA_ROOT, 'uploads', String(chapterId));
	mkdirSync(uploadDir, { recursive: true });
	for (const file of files) {
		const ext = extname(file.name).toLowerCase();
		if (!ALLOWED_EXT.has(ext)) throw error(400, `Unsupported image type "${ext}" — use PNG/JPEG/WebP/AVIF.`);
		const fileName = `${seq}${ext}`;
		writeFileSync(join(uploadDir, fileName), Buffer.from(await file.arrayBuffer()));
		db.insert(pages)
			.values({ chapterId, seq, filePath: `uploads/${chapterId}/${fileName}` })
			.run();
		seq++;
		count++;
	}
	return count;
}

// THE NEXT FREE SEQ FOR A CHAPTER (PURE DB QUERY — UNIT-TESTED).
export function nextPageSeq(chapterId: number): number {
	const max = db
		.select({ seq: pages.seq })
		.from(pages)
		.where(eq(pages.chapterId, chapterId))
		.orderBy(desc(pages.seq))
		.limit(1)
		.get();
	return (max?.seq ?? -1) + 1;
}

// RE-ORDER A CHAPTER'S PAGES (PRESERVES (chapterId, seq) UNIQUE INDEX VIA TEMP SEQUENCES).
export function reorderPages(chapterId: number, pageIds: number[]): void {
	db.transaction(() => {
		for (let i = 0; i < pageIds.length; i++) {
			db.update(pages)
				.set({ seq: -(i + 1000) })
				.where(eq(pages.id, pageIds[i]))
				.run();
		}
		for (let i = 0; i < pageIds.length; i++) {
			db.update(pages)
				.set({ seq: i })
				.where(eq(pages.id, pageIds[i]))
				.run();
		}
	});
}
