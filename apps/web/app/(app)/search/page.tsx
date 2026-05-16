import { SearchForm } from '@/components/search/SearchForm';

export default function SearchPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Chemistry-native search</h1>
      <SearchForm />
    </div>
  );
}
