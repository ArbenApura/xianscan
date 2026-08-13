<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { get } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { settings } from '$lib/stores/settings';
	import { ripple } from '$lib/actions/ripple';
	import GlossaryPanel from '$lib/components/GlossaryPanel.svelte';
	import LanguagePicker from '$lib/components/ui/LanguagePicker.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Sparkles from 'lucide-svelte/icons/sparkles';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Globe from 'lucide-svelte/icons/globe';

	type Book = { id: string; title: string; sourceLang: string; targetLang: string };

	let sourceLang = get(settings).sourceLang;
	let targetLang = get(settings).targetLang;

	let scope: 'global' | 'book' = 'global';
	let books: Book[] = [];
	let selectedBookId = '';
	let isExtracting = false;

	$: initialBookId = $page.url.searchParams.get('bookId');

	onMount(async () => {
		try {
			const res = await fetch('/api/books');
			if (res.ok) {
				const data = await res.json();
				books = data.books || [];
				if (initialBookId && books.some((b) => b.id === initialBookId)) {
					scope = 'book';
					selectedBookId = initialBookId;
				} else if (books.length > 0) {
					selectedBookId = books[0].id;
				}
			}
		} catch {
			// ignore
		}
	});

	$: selectedBook = books.find((b) => b.id === selectedBookId);
	$: bookSelectItems = books.map((b) => ({ value: b.id, label: b.title }));

	async function triggerExtract() {
		if (!selectedBookId) return;
		isExtracting = true;
		try {
			const res = await fetch('/api/glossary/extract', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ bookId: selectedBookId }),
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ message: 'Extraction failed' }));
				throw new Error(err.message || 'Extraction failed');
			}
			const data = await res.json();
			toast.success(`Extracted ${data.added} new term(s) (${data.skipped} existing skipped).`);
			// force panel re-render
			selectedBookId = selectedBookId;
		} catch (e) {
			toast.error((e as Error).message || 'Extraction failed.');
		} finally {
			isExtracting = false;
		}
	}
</script>

<svelte:head>
	<title>{scope === 'global' ? 'Global' : 'Book'} Glossary — Manua Translator</title>
</svelte:head>

<div class="mx-auto min-h-full w-full max-w-4xl px-4 py-8 sm:px-6">
	<!-- BACK NAVIGATION -->
	<div class="mb-4 flex items-center justify-between">
		<a href="/app/" use:ripple class="text-sm opacity-60 hover:text-[#b23a2e]">← Library</a>
	</div>

	<!-- PAGE HEADER -->
	<header class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
		<div>
			<h1 class="text-2xl font-bold leading-tight sm:text-3xl">Glossary Terms</h1>
			<p class="mt-1 text-sm opacity-60">
				{scope === 'global'
					? 'Global terms applied to every book matching the selected language pair'
					: 'Private terms specific to the selected book'}
			</p>
		</div>

		<!-- SCOPE SWITCHER TABS -->
		<div class="flex items-center gap-1 rounded-xl bg-black/5 p-1 dark:bg-white/5">
			<button
				type="button"
				class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					scope === 'global'
						? 'bg-white font-semibold text-black shadow-sm dark:bg-white/10 dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				on:click={() => (scope = 'global')}
			>
				<Globe size={14} /> Global Scope
			</button>
			<button
				type="button"
				class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					scope === 'book'
						? 'bg-white font-semibold text-black shadow-sm dark:bg-white/10 dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				on:click={() => (scope = 'book')}
			>
				<BookOpen size={14} /> Book Scope
			</button>
		</div>
	</header>

	<!-- SCOPE CONTROLS -->
	{#if scope === 'global'}
		<div class="mb-5 grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-end">
			<div class="min-w-0">
				<span class="mb-1 block text-xs opacity-50">Source (original)</span>
				<LanguagePicker value={sourceLang} on:change={(e) => (sourceLang = e.detail)} />
			</div>
			<span class="hidden pb-2 text-center opacity-40 sm:block">→</span>
			<div class="min-w-0">
				<span class="mb-1 block text-xs opacity-50">Target (translation)</span>
				<LanguagePicker value={targetLang} on:change={(e) => (targetLang = e.detail)} />
			</div>
		</div>

		{#key `${sourceLang}>${targetLang}`}
			<GlossaryPanel scope="global" {sourceLang} {targetLang} />
		{/key}
	{:else}
		<!-- BOOK SCOPE SELECTOR & AUTO-EXTRACT TRIGGER -->
		<div class="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
			<div class="w-full sm:max-w-xs">
				<span class="mb-1 block text-xs opacity-50">Select Book</span>
				{#if books.length > 0}
					<Select
						items={bookSelectItems}
						value={selectedBookId}
						on:change={(e) => (selectedBookId = String(e.detail))}
					/>
				{:else}
					<p class="text-xs opacity-50">No books created yet.</p>
				{/if}
			</div>

			{#if selectedBookId}
				<Button
					variant="outline"
					size="sm"
					disabled={isExtracting}
					on:click={triggerExtract}
					class="flex items-center gap-1.5"
				>
					<Sparkles size={14} class="text-amber-500" />
					{isExtracting ? 'Extracting Terms...' : 'Auto-Extract AI Terms'}
				</Button>
			{/if}
		</div>

		{#if selectedBookId && selectedBook}
			{#key selectedBookId}
				<GlossaryPanel scope="book" bookId={selectedBookId} bookTitle={selectedBook.title} />
			{/key}
		{:else if books.length === 0}
			<div class="rounded-xl border p-8 text-center opacity-60 dark:border-white/10">
				Create a book first to add book-specific glossary terms.
			</div>
		{/if}
	{/if}
</div>
