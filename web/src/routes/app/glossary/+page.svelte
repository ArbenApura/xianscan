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
	import ArrowLeft from 'lucide-svelte/icons/arrow-left';
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
	<title>{scope === 'global' ? 'Global' : 'Book'} Glossary — Manhua Translator</title>
</svelte:head>

<!-- GLOSSARY MANAGEMENT DASHBOARD -->
<div class="flex flex-col gap-6">
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

	<!-- HERO HEADER CARD -->
	<div class="relative overflow-hidden rounded-2xl border border-black/[0.08] bg-white/50 p-6 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.02]">
		<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
			<div>
				<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">Glossary Terms</h1>
				<p class="mt-1 text-sm opacity-60">
					{scope === 'global'
						? 'Global terms applied to every book matching the selected language pair.'
						: 'Book-specific terms and character names private to the selected series.'}
				</p>
			</div>

			<!-- SCOPE SWITCHER TABS -->
			<div class="flex items-center gap-1 rounded-xl bg-black/[0.04] p-1 dark:bg-white/[0.04]">
				<button
					type="button"
					class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
						scope === 'global'
							? 'bg-white font-semibold text-black shadow-xs dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					on:click={() => (scope = 'global')}
					use:ripple
				>
					<Globe size={14} />
					<span>Global Scope</span>
				</button>
				<button
					type="button"
					class={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
						scope === 'book'
							? 'bg-white font-semibold text-black shadow-xs dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					on:click={() => (scope = 'book')}
					use:ripple
				>
					<BookOpen size={14} />
					<span>Book Scope</span>
				</button>
			</div>
		</div>
	</div>

	<!-- SCOPE CONTROLS CARD -->
	{#if scope === 'global'}
		<div class="rounded-2xl border border-black/[0.08] bg-white/40 p-4 dark:border-white/[0.06] dark:bg-white/[0.02]">
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-end">
				<div class="min-w-0">
					<span class="mb-1 block text-xs font-semibold opacity-60">Source (original)</span>
					<LanguagePicker value={sourceLang} on:change={(e) => (sourceLang = e.detail)} />
				</div>
				<span class="hidden pb-2 text-center text-sm font-bold opacity-40 sm:block">→</span>
				<div class="min-w-0">
					<span class="mb-1 block text-xs font-semibold opacity-60">Target (translation)</span>
					<LanguagePicker value={targetLang} on:change={(e) => (targetLang = e.detail)} />
				</div>
			</div>
		</div>

		{#key `${sourceLang}>${targetLang}`}
			<GlossaryPanel scope="global" {sourceLang} {targetLang} />
		{/key}
	{:else}
		<!-- BOOK SCOPE SELECTOR & AUTO-EXTRACT TRIGGER -->
		<div class="rounded-2xl border border-black/[0.08] bg-white/40 p-4 dark:border-white/[0.06] dark:bg-white/[0.02]">
			<div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
				<div class="w-full sm:max-w-xs">
					<span class="mb-1 block text-xs font-semibold opacity-60">Select Series</span>
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
						variant="secondary"
						size="sm"
						disabled={isExtracting}
						on:click={triggerExtract}
						class="flex items-center gap-1.5"
					>
						<Sparkles size={14} class="text-amber-500" />
						<span>{isExtracting ? 'Extracting Terms...' : 'Auto-Extract AI Terms'}</span>
					</Button>
				{/if}
			</div>
		</div>

		{#if selectedBookId && selectedBook}
			{#key selectedBookId}
				<GlossaryPanel scope="book" bookId={selectedBookId} bookTitle={selectedBook.title} />
			{/key}
		{:else if books.length === 0}
			<div class="rounded-2xl border border-dashed border-black/15 p-12 text-center text-sm opacity-60 dark:border-white/15">
				Create a book first to add book-specific glossary terms.
			</div>
		{/if}
	{/if}
</div>
