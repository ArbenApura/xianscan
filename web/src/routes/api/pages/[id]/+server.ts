// PAGE DETAIL & DELETE ENDPOINT.
import { error, json } from '@sveltejs/kit';
import { eq } from 'drizzle-orm';
import { db } from '$lib/server/db';
import { pages } from '$lib/server/db/schema';
import type { RequestHandler } from './$types';

export const DELETE: RequestHandler = async ({ params }) => {
	const pageId = Number(params.id);
	if (!Number.isInteger(pageId)) throw error(400, 'Invalid page id.');
	const [p] = db.select().from(pages).where(eq(pages.id, pageId)).all();
	if (!p) throw error(404, 'Page not found.');

	db.delete(pages).where(eq(pages.id, pageId)).run();
	return json({ ok: true });
};
