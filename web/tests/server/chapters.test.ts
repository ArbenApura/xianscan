// CHAPTER HELPERS TESTS — THE UPLOAD SEQ-CONTINUATION REGRESSION (A SECOND UPLOAD USED TO 500 ON
// THE (chapterId, seq) UNIQUE INDEX).
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { eq } from 'drizzle-orm';
import { getTestDb, resetDb, seedBook, seedChapter, seedPage, type TestDb } from '../helpers/db';
import { nextPageSeq, reorderPages } from '$lib/server/chapters';
import { pages } from '$lib/server/db/schema';

vi.mock('$lib/server/db', async () => ({ db: (await import('../helpers/db')).getTestDb() }));

// -- STATES -- //

let db: TestDb;

// -- LIFECYCLES -- //

beforeEach(() => {
	db = getTestDb();
	resetDb();
});

// -- TESTS -- //

describe('nextPageSeq & reorderPages', () => {
	it('starts at 0 for an empty chapter', () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		expect(nextPageSeq(chapter.id)).toBe(0);
	});

	it('continues after the highest existing seq (regression: every upload used to restart at 0)', () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		seedPage(db, { chapterId: chapter.id, seq: 0 });
		seedPage(db, { chapterId: chapter.id, seq: 1 });
		expect(nextPageSeq(chapter.id)).toBe(2);
	});

	it('is per-chapter (other chapters do not affect the counter)', () => {
		seedBook(db, { id: 'b1' });
		const c1 = seedChapter(db, { bookId: 'b1', seq: 0 });
		const c2 = seedChapter(db, { bookId: 'b1', seq: 1 });
		seedPage(db, { chapterId: c1.id, seq: 0 });
		seedPage(db, { chapterId: c1.id, seq: 1 });
		seedPage(db, { chapterId: c1.id, seq: 2 });
		expect(nextPageSeq(c1.id)).toBe(3);
		expect(nextPageSeq(c2.id)).toBe(0);
	});

	it('inserting at the returned seq never collides (the original 500)', () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		seedPage(db, { chapterId: chapter.id, seq: 0 });
		const seq = nextPageSeq(chapter.id);
		expect(() => {
			db.insert(pages).values({ chapterId: chapter.id, seq, filePath: 'uploads/x.png' }).run();
		}).not.toThrow();
		const rows = db.select().from(pages).where(eq(pages.chapterId, chapter.id)).all();
		expect(rows).toHaveLength(2);
	});

	it('reorders page sequence numbers without unique index collisions', () => {
		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { bookId: 'b1', seq: 0 });
		const p0 = seedPage(db, { chapterId: chapter.id, seq: 0 });
		const p1 = seedPage(db, { chapterId: chapter.id, seq: 1 });
		const p2 = seedPage(db, { chapterId: chapter.id, seq: 2 });

		// REVERSE THE ORDER: [p2, p1, p0]
		reorderPages(chapter.id, [p2.id, p1.id, p0.id]);

		const rows = db.select().from(pages).where(eq(pages.chapterId, chapter.id)).orderBy(pages.seq).all();
		expect(rows).toHaveLength(3);
		expect(rows[0].id).toBe(p2.id);
		expect(rows[0].seq).toBe(0);
		expect(rows[1].id).toBe(p1.id);
		expect(rows[1].seq).toBe(1);
		expect(rows[2].id).toBe(p0.id);
		expect(rows[2].seq).toBe(2);
	});
});
