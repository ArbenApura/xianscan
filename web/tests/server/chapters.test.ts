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

	it('stitchPageWithNext manually merges a page with the next page', async () => {
		const fs = await import('node:fs');
		const path = await import('node:path');
		const os = await import('node:os');
		const { stitchPageWithNext } = await import('$lib/server/chapters');

		const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'manua-test-'));
		fs.mkdirSync(path.join(dataRoot, 'uploads', '1'), { recursive: true });
		fs.writeFileSync(path.join(dataRoot, 'uploads/1/0.png'), Buffer.from('page0'));
		fs.writeFileSync(path.join(dataRoot, 'uploads/1/1.png'), Buffer.from('page1'));

		seedBook(db, { id: 'b1' });
		const chapter = seedChapter(db, { id: 1, bookId: 'b1', seq: 0 });
		const p0 = seedPage(db, { chapterId: chapter.id, seq: 0, filePath: 'uploads/1/0.png' });
		seedPage(db, { chapterId: chapter.id, seq: 1, filePath: 'uploads/1/1.png' });

		const fakePipeline = {
			preprocess: async (b: Buffer) => b,
			analyze: async () => ({ width: 100, height: 100, backend: 'comic-ctd', regions: [] }),
			clean: async (b: Buffer) => b,
			health: async () => ({ status: 'ok', detector: 'comic-ctd', inpainter: 'lama' }),
			stitch: async (top: Buffer, bot: Buffer) => Buffer.concat([top, bot]),
		};

		await stitchPageWithNext(p0.id, fakePipeline, dataRoot);

		const remaining = db.select().from(pages).where(eq(pages.chapterId, chapter.id)).all();
		expect(remaining).toHaveLength(1);
		expect(remaining[0].seq).toBe(0);
		expect(fs.readFileSync(path.join(dataRoot, remaining[0].filePath)).toString()).toBe('page0page1');

		fs.rmSync(dataRoot, { recursive: true, force: true });
	});
});


