# VLAN update and reservation semantics

DHCP reservations are managed independently from a VLAN's general settings.
REST `PUT /api/vlans/{id}`, the Web UI, the CLI edit flow, and staging
`update_vlan` preserve existing reservations when `dhcp_reservations` is
omitted. Supplying that field explicitly intentionally replaces the collection;
the per-reservation CRUD endpoints remain the normal way to add, edit, or
delete an individual reservation.

Configuration import is a full configuration replacement and therefore uses
the reservation list present in the imported document. This distinction avoids
silently dropping reservations during an unrelated interactive VLAN edit while
preserving an explicit bulk-replacement mechanism.
