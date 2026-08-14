<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button, TextField, Modal, ConfirmDialog, ActionMenu, LanguagePicker } from '$lib/components/ui';
	import { ripple } from '$lib/actions/ripple';
	import { SOURCE_LANGUAGE_OPTIONS, TARGET_LANGUAGE_OPTIONS } from '$lib/languages';
	import { settings } from '$lib/stores/settings';
	// IMPORTED ICONS
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Plus from 'lucide-svelte/icons/plus';
	import Search from 'lucide-svelte/icons/search';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import ExternalLink from 'lucide-svelte/icons/external-link';
	import Layers from 'lucide-svelte/icons/layers';

	// -- TYPES -- //

	interface Book {
		id: string;
		title: string;
		sourceLang: string;
		targetLang: string;
		chapterCount: number;
	}

	// -- STATES -- //

	let books: Book[] = [];
	let loading = true;
	let title = '';
	let sourceLang = $settings.sourceLang;
	let targetLang = $settings.targetLang;
	let creating = false;
	let searchQuery = '';
	let createModalOpen = false;

	// DELETION CONFIRMATION
	let bookToDelete: Book | null = null;
	let deleteConfirmOpen = false;
	let deleting = false;

	// -- LIFECYCLES -- //

	onMount(() => {
		loadBooks();
	});

	// -- FUNCTIONS -- //

	async function loadBooks() {
		try {
			const resp = await fetch('/api/books');
			books = (await resp.json()).books;
		} catch {
			toast.error('Could not load books.');
		} finally {
			loading = false;
		}
	}

	async function createBook() {
		const t = title.trim();
		if (!t) return;
		creating = true;
		try {
			const resp = await fetch('/api/books', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: t,
					sourceLang,
					targetLang,
				}),
			});
			if (!resp.ok) throw new Error('create failed');
			const { id } = await resp.json();
			toast.success('Book created.');
			createModalOpen = false;
			title = '';
			goto(`/app/books/${id}`);
		} catch {
			toast.error('Could not create the book.');
		} finally {
			creating = false;
		}
	}

	function promptDeleteBook(book: Book) {
		bookToDelete = book;
		deleteConfirmOpen = true;
	}

	async function confirmDeleteBook() {
		if (!bookToDelete) return;
		deleting = true;
		try {
			const resp = await fetch(`/api/books/${bookToDelete.id}`, {
				method: 'DELETE',
			});
			if (!resp.ok) throw new Error('Delete failed');
			toast.success(`Deleted "${bookToDelete.title}".`);
			books = books.filter((b) => b.id !== bookToDelete?.id);
		} catch {
			toast.error('Could not delete the book.');
		} finally {
			deleting = false;
			deleteConfirmOpen = false;
			bookToDelete = null;
		}
	}

	// REACTIVE FILTERED BOOKS
	$: filteredBooks = books.filter(
		(b) =>
			b.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
			b.sourceLang.toLowerCase().includes(searchQuery.toLowerCase()) ||
			b.targetLang.toLowerCase().includes(searchQuery.toLowerCase()),
	);

	$: totalChapters = books.reduce((sum, b) => sum + (b.chapterCount || 0), 0);
</script>

<svelte:head>
	<title>Library — Manua Translator</title>
</svelte:head>

<!-- LIBRARY DASHBOARD -->
<div class="flex flex-col gap-6 py-8">
	<!-- HEADER SECTION -->
	<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
		<div>
			<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">Library</h1>
			<p class="mt-1 text-sm opacity-60">Upload manhua chapters, manage terms, and translate page by page.</p>
		</div>

		<div class="flex items-center gap-3">
			<Button variant="primary" on:click={() => (createModalOpen = true)}>
				<Plus size={16} /> New Book
			</Button>
		</div>
	</div>

	<!-- STATS SUMMARY BAR -->
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
		<div class="rounded-xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="flex items-center gap-2 text-xs font-semibold opacity-60">
				<BookOpen size={14} class="text-[#b23a2e] dark:text-[#e08a63]" /> Total Series
			</div>
			<div class="mt-1 text-2xl font-bold">{books.length}</div>
		</div>

		<div class="rounded-xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="flex items-center gap-2 text-xs font-semibold opacity-60">
				<Layers size={14} class="text-amber-600 dark:text-amber-400" /> Total Chapters
			</div>
			<div class="mt-1 text-2xl font-bold">{totalChapters}</div>
		</div>

		<div class="col-span-2 rounded-xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03] sm:col-span-1">
			<div class="flex items-center justify-between text-xs font-semibold opacity-60">
				<span>Global Glossary</span>
				<a href="/app/glossary" class="text-[#b23a2e] hover:underline dark:text-[#e08a63]">Manage →</a>
			</div>
			<div class="mt-1 text-xs opacity-70">Applies automatically to translation prompts</div>
		</div>
	</div>

	<!-- SEARCH & TOOLBAR -->
	<div class="flex w-full items-center justify-between gap-3">
		<div class="relative min-w-0 flex-1 max-w-md">
			<Search size={15} class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
			<input
				bind:value={searchQuery}
				type="search"
				placeholder="Search books by title or language..."
				class="w-full rounded-xl border border-black/10 bg-transparent py-2 pl-9 pr-3 text-sm outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
			/>
		</div>
	</div>

	<!-- BOOK LIST GRID -->
	{#if loading}
		<div class="grid w-full gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each [1, 2, 3] as _}
				<div class="h-32 animate-pulse rounded-xl border border-black/[0.06] bg-black/[0.03] dark:border-white/[0.06] dark:bg-white/[0.03]"></div>
			{/each}
		</div>
	{:else if books.length === 0}
		<div class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-black/15 py-16 text-center dark:border-white/15">
			<div class="flex h-12 w-12 items-center justify-center rounded-full bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]">
				<BookOpen size={24} />
			</div>
			<h2 class="mt-4 text-base font-semibold">No books in your library</h2>
			<p class="mt-1 max-w-sm text-xs opacity-60">Create your first book series to start uploading chapter images for translation.</p>
			<Button variant="primary" size="sm" class="mt-4" on:click={() => (createModalOpen = true)}>
				<Plus size={14} /> Create First Book
			</Button>
		</div>
	{:else if filteredBooks.length === 0}
		<p class="py-8 text-center text-sm opacity-60">No books found matching "{searchQuery}".</p>
	{:else}
		<ul class="grid w-full gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each filteredBooks as book (book.id)}
				<li class="group relative flex flex-col justify-between rounded-xl border border-black/[0.08] bg-white/40 p-5 transition-all duration-200 hover:border-[#b23a2e]/40 hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02]">
					<div>
						<div class="flex items-start justify-between gap-2">
							<a href={`/app/books/${book.id}`} class="font-bold text-base tracking-tight hover:text-[#b23a2e] dark:hover:text-[#e08a63]">
								{book.title}
							</a>

							<ActionMenu
								items={[
									{ value: 'open', label: 'Open Book', icon: ExternalLink },
									{ value: 'delete', label: 'Delete Book', icon: Trash2, danger: true },
								]}
								on:select={(e) => {
									if (e.detail === 'open') goto(`/app/books/${book.id}`);
									else if (e.detail === 'delete') promptDeleteBook(book);
								}}
							/>
						</div>

						<div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
							<span class="rounded-md bg-[#b23a2e]/10 px-2 py-0.5 font-semibold text-[#b23a2e] dark:text-[#e08a63]">
								{book.sourceLang} → {book.targetLang}
							</span>
							<span class="opacity-60">
								{book.chapterCount} {book.chapterCount === 1 ? 'chapter' : 'chapters'}
							</span>
						</div>
					</div>

					<div class="mt-5 flex items-center justify-between border-t border-black/[0.04] pt-3 text-xs opacity-60 dark:border-white/[0.04]">
						<span>Self-hosted pipeline</span>
						<a
							href={`/app/books/${book.id}`}
							class="font-medium text-[#b23a2e] group-hover:underline dark:text-[#e08a63]"
						>
							View →
						</a>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<!-- CREATE BOOK MODAL -->
<Modal open={createModalOpen} title="Create New Book Series" size="sm" on:close={() => (createModalOpen = false)}>
	<form class="flex flex-col gap-4" on:submit|preventDefault={createBook}>
		<TextField
			bind:value={title}
			label="Book Title"
			placeholder="e.g. 星尘 (Stardust)"
		/>

		<div class="grid grid-cols-2 gap-3">
			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Source Language</span>
				<LanguagePicker bind:value={sourceLang} />
			</div>

			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Target Language</span>
				<LanguagePicker bind:value={targetLang} />
			</div>
		</div>
	</form>

	<svelte:fragment slot="footer">
		<Button on:click={() => (createModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={creating || !title.trim()} loading={creating} on:click={createBook}>
			Create Book
		</Button>
	</svelte:fragment>
</Modal>

<!-- DELETE CONFIRMATION DIALOG -->
<ConfirmDialog
	open={deleteConfirmOpen}
	title="Delete Book Series?"
	message={`Are you sure you want to delete "${bookToDelete?.title}"? All chapters, pages, and cached translations for this book will be permanently deleted.`}
	confirmLabel="Delete Book"
	variant="danger"
	on:confirm={confirmDeleteBook}
	on:cancel={() => (deleteConfirmOpen = false)}
/>
