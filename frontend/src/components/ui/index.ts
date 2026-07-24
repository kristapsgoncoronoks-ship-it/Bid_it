// InvoiceIQ UI kit — the reusable, accessible primitives every page composes from.
// Import from one place:  import { Button, Badge, DataTable } from "../components/ui";

// Actions & status
export { Button } from "./Button";
export type { ButtonProps } from "./Button";
export { Badge, Dot } from "./Badge";
export type { Tone } from "./Badge";
export { StatusBadge, STATUS_MAP } from "./StatusBadge";
export type { StatusBadgeProps } from "./StatusBadge";

// Surfaces & layout
export { Card, StatCard } from "./Card";
export { PageHeader } from "./PageHeader";
export type { PageHeaderProps } from "./PageHeader";
export { Breadcrumbs } from "./Breadcrumbs";
export type { Crumb } from "./Breadcrumbs";
export { Tabs, TabPanel } from "./Tabs";
export type { TabItem, TabsProps } from "./Tabs";

// Data display
export { DataTable } from "./DataTable";
export type { Column, DataTableProps, Align, SortState } from "./DataTable";
export { useSort } from "./useSort";
export { Pagination } from "./Pagination";
export type { PaginationProps } from "./Pagination";
export { SearchInput } from "./SearchInput";
export type { SearchInputProps } from "./SearchInput";
export { FilterBar, FilterSelect, FilterChip } from "./FilterBar";
export type { FilterSelectProps } from "./FilterBar";
export { Timeline } from "./Timeline";
export type { TimelineEvent } from "./Timeline";

// Forms
export { FormField, TextInput, Select, Textarea, inputClass } from "./Form";
export type { FormFieldProps, FieldControlProps } from "./Form";
export { CurrencyInput } from "./CurrencyInput";
export type { CurrencyInputProps } from "./CurrencyInput";
export { DateInput } from "./DateInput";
export type { DateInputProps } from "./DateInput";
export { TaxRateInput } from "./TaxRateInput";
export type { TaxRateInputProps } from "./TaxRateInput";
export { FileUpload } from "./FileUpload";
export type { FileUploadProps } from "./FileUpload";

// Overlays
export { Modal } from "./Modal";
export type { ModalProps, ModalSize } from "./Modal";
export { Drawer } from "./Drawer";
export type { DrawerProps, DrawerSide, DrawerSize } from "./Drawer";
export { ConfirmDialog } from "./ConfirmDialog";
export type { ConfirmDialogProps } from "./ConfirmDialog";
export { Portal } from "./Portal";
export { useFocusTrap } from "./useFocusTrap";

// Async / feedback states
export { EmptyState } from "./EmptyState";
export { ErrorState } from "./ErrorState";
export { QueryState } from "./QueryState";
export { Spinner } from "./Spinner";
export { Skeleton, SkeletonText } from "./Skeleton";
