// BOOK ACCESS HELPERS — SINGLE-USER APP, SO "OWNERSHIP" REDUCES TO "THE BOOK EXISTS" (xianslate'S
// assertBookOwner BECOMES assertBookExists). GROWS WITH THE BOOKS UI (PHASE 5).

// IMPORTED DEP-MODULES
import { error } from '@sveltejs/kit';
import { eq } from 'drizzle-orm';
// IMPORTED MODULES
import { db } from './db';
import { books } from './db/schema';

// -- FUNCTIONS -- //

// VALIDATE THE TARGET BOOK EXISTS — OTHERWISE BOOK-SCOPED ROWS ORPHAN. THROWS 404 WHEN IT IS GONE.
export async function assertBookExists(bookId: string): Promise<void> {
	const [b] = await db.select({ id: books.id }).from(books).where(eq(books.id, bookId)).limit(1);
	if (!b) throw error(404, 'Book not found.');
}
