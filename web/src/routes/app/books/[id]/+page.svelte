<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button, TextField, Badge, Modal, ConfirmDialog, ActionMenu } from '$lib/components/ui';
	import { ripple } from '$lib/actions/ripple';
	// IMPORTED ICONS
	import ArrowLeft from 'lucide-svelte/icons/arrow-left';
	import Plus from 'lucide-svelte/icons/plus';
	import Search from 'lucide-svelte/icons/search';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import ExternalLink from 'lucide-svelte/icons/external-link';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Languages from 'lucide-svelte/icons/languages';
	import Layers from 'lucide-svelte/icons/layers';

	// -- TYPES -- //

	interface Chapter {
		id: number;
		title: string;
		seq: number;
		status: 'pending' | 'processing' | 'done' | 'error';
		pageCount: number;
	}

	// -- STATES -- //

	let book: { id: string; title: string; sourceLang: string; targetLang: string } | null = null;
	let chapters: Chapter[] = [];
	let loading = true;
	let chapterTitle = '';
	let creating = false;
	let searchQuery = '';
	let createModalOpen = false;

	// DELETION STATES
	let chapterToDelete: Chapter | null = null;
	let deleteConfirmOpen = false;
	let deleting = false;

	// -- LIFECYCLES -- //

	onMount(async () => {
		await reload();
	});

	// -- FUNCTIONS -- //

	async function reload() {
		try {
			const resp = await fetch(`/api/books/${$page.params.id}`);
			if (!resp.ok) throw new Error('not found');
			const data = await resp.json();
			book = data.book;
			chapters = data.chapters;
		} catch {
			toast.error('Could not load the book.');
		} finally {
			loading = false;
		}
	}

	async function createChapter() {
		creating = true;
		try {
			const resp = await fetch(`/api/books/${$page.params.id}/chapters`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ title: chapterTitle.trim() }),
			});
			if (!resp.ok) throw new Error('create failed');
			const { id: chapterId } = await resp.json();
			toast.success('Chapter created.');
			chapterTitle = '';
			createModalOpen = false;
			goto(`/app/books/${$page.params.id}/chapters/${chapterId}`);
		} catch {
			toast.error('Could not create the chapter.');
		} finally {
			creating = false;
		}
	}

	function promptDeleteChapter(chap: Chapter) {
		chapterToDelete = chap;
		deleteConfirmOpen = true;
	}

	async function confirmDeleteChapter() {
		if (!chapterToDelete) return;
		deleting = true;
		try {
			const resp = await fetch(`/api/chapters/${chapterToDelete.id}`, { method: 'DELETE' });
			if (!resp.ok) throw new Error('Delete failed');
			toast.success('Chapter deleted.');
			chapters = chapters.filter((c) => c.id !== chapterToDelete?.id);
		} catch {
			toast.error('Could not delete chapter.');
		} finally {
			deleting = false;
			deleteConfirmOpen = false;
			chapterToDelete = null;
		}
	}

	const statusVariant: Record<Chapter['status'], 'neutral' | 'amber' | 'jade' | 'cinnabar'> = {
		pending: 'neutral',
		processing: 'amber',
		done: 'jade',
		error: 'cinnabar',
	};

	$: filteredChapters = chapters.filter(
		(c) =>
			(c.title || `Chapter ${c.seq + 1}`).toLowerCase().includes(searchQuery.toLowerCase()) ||
			c.status.toLowerCase().includes(searchQuery.toLowerCase()),
	);

	$: totalPages = chapters.reduce((sum, c) => sum + (c.pageCount || 0), 0);
</script>

<svelte:head>
	<title>{book ? `${book.title} — Manua Translator` : 'Book Details'}</title>
</svelte:head>

<!-- BOOK DETAIL & CHAPTER MANAGEMENT -->
<div class="flex flex-col gap-6 py-8">
	<!-- BACK BUTTON -->
	<div>
		<a
			href="/app/"
			class="inline-flex items-center gap-1.5 text-xs font-semibold opacity-60 transition hover:opacity-100 hover:text-[#b23a2e]"
			use:ripple
		>
			<ArrowLeft size={14} /> Back to Library
		</a>
	</div>

	{#if loading}
		<div class="h-48 animate-pulse rounded-2xl border border-black/[0.06] bg-black/[0.03] dark:border-white/[0.06] dark:bg-white/[0.03]"></div>
	{:else if book}
		<!-- HERO HEADER CARD -->
		<div class="relative overflow-hidden rounded-2xl border border-black/[0.08] bg-white/50 p-6 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.02]">
			<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<div class="flex items-center gap-2">
						<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">{book.title}</h1>
						<span class="rounded-md bg-[#b23a2e]/10 px-2.5 py-0.5 text-xs font-semibold text-[#b23a2e] dark:text-[#e08a63]">
							{book.sourceLang} → {book.targetLang}
						</span>
					</div>
					<p class="mt-1 text-sm opacity-60">
						{chapters.length} chapter{chapters.length === 1 ? '' : 's'} · {totalPages} total pages uploaded
					</p>
				</div>

				<div class="flex flex-wrap items-center gap-3">
					<Button variant="primary" on:click={() => (createModalOpen = true)}>
						<Plus size={16} /> New Chapter
					</Button>
				</div>
			</div>
		</div>

		<!-- SEARCH & TOOLBAR -->
		<div class="flex w-full items-center justify-between gap-3">
			<div class="relative min-w-0 flex-1 max-w-md">
				<Search size={15} class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
				<input
					bind:value={searchQuery}
					type="search"
					placeholder="Search chapters..."
					class="w-full rounded-xl border border-black/10 bg-transparent py-2 pl-9 pr-3 text-sm outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
				/>
			</div>
		</div>

		<!-- CHAPTER GRID -->
		{#if chapters.length === 0}
			<div class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-black/15 py-16 text-center dark:border-white/15">
				<div class="flex h-12 w-12 items-center justify-center rounded-full bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]">
					<Layers size={24} />
				</div>
				<h2 class="mt-4 text-base font-semibold">No chapters yet</h2>
				<p class="mt-1 max-w-sm text-xs opacity-60">Create your first chapter to start uploading page images for text detection & translation.</p>
				<Button variant="primary" size="sm" class="mt-4" on:click={() => (createModalOpen = true)}>
					<Plus size={14} /> Create Chapter
				</Button>
			</div>
		{:else if filteredChapters.length === 0}
			<p class="py-8 text-center text-sm opacity-60">No chapters found matching "{searchQuery}".</p>
		{:else}
			<ul class="grid w-full gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each filteredChapters as chapter (chapter.id)}
					<li class="group relative flex flex-col justify-between rounded-xl border border-black/[0.08] bg-white/40 p-5 transition-all duration-200 hover:border-[#b23a2e]/40 hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02]">
						<div>
							<div class="flex items-start justify-between gap-2">
								<a
									href={`/app/books/${$page.params.id}/chapters/${chapter.id}`}
									class="font-bold text-base tracking-tight hover:text-[#b23a2e] dark:hover:text-[#e08a63]"
								>
									{chapter.title || `Chapter ${chapter.seq + 1}`}
								</a>

								<ActionMenu
									items={[
										{ value: 'open', label: 'Open Workspace', icon: ExternalLink },
										{ value: 'delete', label: 'Delete Chapter', icon: Trash2, danger: true },
									]}
									on:select={(e) => {
										if (e.detail === 'open') goto(`/app/books/${$page.params.id}/chapters/${chapter.id}`);
										else if (e.detail === 'delete') promptDeleteChapter(chapter);
									}}
								/>
							</div>

							<div class="mt-3 flex items-center justify-between text-xs">
								<Badge variant={statusVariant[chapter.status]}>
									{chapter.status.toUpperCase()}
								</Badge>
								<span class="opacity-60">
									{chapter.pageCount} {chapter.pageCount === 1 ? 'page' : 'pages'}
								</span>
							</div>
						</div>

						<div class="mt-5 flex items-center justify-between border-t border-black/[0.04] pt-3 text-xs opacity-60 dark:border-white/[0.04]">
							<span>Seq #{chapter.seq + 1}</span>
							<a
								href={`/app/books/${$page.params.id}/chapters/${chapter.id}`}
								class="font-medium text-[#b23a2e] group-hover:underline dark:text-[#e08a63]"
							>
								Open Reader →
							</a>
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>

<!-- CREATE CHAPTER MODAL -->
<Modal open={createModalOpen} title="Create New Chapter" size="sm" on:close={() => (createModalOpen = false)}>
	<form class="flex flex-col gap-4" on:submit|preventDefault={createChapter}>
		<TextField
			bind:value={chapterTitle}
			label="Chapter Title"
			placeholder="e.g. 第1话 (Chapter 1)"
			required
		/>
	</form>

	<svelte:fragment slot="footer">
		<Button on:click={() => (createModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={creating} loading={creating} on:click={createChapter}>
			Create & Open
		</Button>
	</svelte:fragment>
</Modal>

<!-- DELETE CONFIRMATION DIALOG -->
<ConfirmDialog
	open={deleteConfirmOpen}
	title="Delete Chapter?"
	description={`Are you sure you want to delete "${chapterToDelete?.title || 'Chapter'} "? All uploaded page images and translation output for this chapter will be permanently removed.`}
	confirmLabel="Delete Chapter"
	destructive
	loading={deleting}
	on:confirm={confirmDeleteChapter}
	on:cancel={() => (deleteConfirmOpen = false)}
/>
