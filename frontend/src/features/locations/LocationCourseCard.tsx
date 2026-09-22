// Обёртка над паспортом трассы: пока треки собираются через админку, блок
// виден только админу. Открытие для всех — флагом tracks_public_enabled.

import { useOptionalUser } from "../../lib/useOptionalUser";
import { LocationCourseSection } from "./LocationCourseSection";

export function LocationCourseCard({ slug }: { slug: string }) {
  const user = useOptionalUser();
  if (!user?.is_admin) {
    return null;
  }
  return <LocationCourseSection slug={slug} />;
}
