"use client";

/**
 * Group membership: the groups you are in, and asking to join others.
 *
 * Membership is many-to-many, so this renders a LIST of groups rather than "my
 * group". Joining is a request a group admin approves -- self-service join was
 * removed -- so a group you are not in offers "request to join", and an admin
 * sees a pending queue on their own group's card.
 *
 * Authorization mirrors the backend exactly: `role === "admin"` in a group (or
 * being a global admin) is what unlocks editing, deleting, removing members and
 * deciding requests. `created_by_user_id` is shown as provenance and grants
 * nothing -- checking it here is what used to leave every non-founder admin
 * looking at buttons the server would refuse.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Crown,
  Loader2,
  LogOut,
  Pencil,
  Plus,
  Trash2,
  User,
  UserMinus,
  Users,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { api, type GroupDetail, type JoinRequest, type MyGroupMembership } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { CreateGroupDialog, EditGroupDialog } from "./GroupDialogs";

export function GroupsSection(): JSX.Element {
  const tg = useTranslations("groups");
  const tCommon = useTranslations("common");
  const queryClient = useQueryClient();
  const { user } = useAuthStore();

  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [editGroup, setEditGroup] = useState<GroupDetail | null>(null);
  const [showBrowseGroups, setShowBrowseGroups] = useState(false);
  const [confirmLeaveId, setConfirmLeaveId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [confirmKick, setConfirmKick] = useState<{ groupId: number; userId: number } | null>(null);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [groupSuccess, setGroupSuccess] = useState<string | null>(null);

  const {
    data: myGroupsData,
    isLoading: isLoadingMyGroups,
    isError: myGroupsFailed,
    error: myGroupsError,
  } = useQuery({
    queryKey: ["myGroups"],
    queryFn: () => api.getMyGroups(),
  });
  const { data: allGroups, isLoading: isLoadingGroups } = useQuery({
    queryKey: ["groups"],
    queryFn: () => api.getGroups(),
    enabled: showBrowseGroups,
  });
  const { data: myRequests } = useQuery({
    queryKey: ["myJoinRequests"],
    queryFn: () => api.listMyJoinRequests(),
  });

  const memberships = myGroupsData?.items ?? [];
  const myGroupIds = new Set(memberships.map((m) => m.group.id));
  const pendingRequests = (myRequests?.items ?? []).filter((r) => r.status === "pending");

  useEffect(() => {
    if (!groupSuccess) return;
    const timer = setTimeout(() => setGroupSuccess(null), 3000);
    return () => clearTimeout(timer);
  }, [groupSuccess]);

  const refresh = (message: string) => {
    queryClient.invalidateQueries({ queryKey: ["myGroups"] });
    queryClient.invalidateQueries({ queryKey: ["groups"] });
    queryClient.invalidateQueries({ queryKey: ["myJoinRequests"] });
    queryClient.invalidateQueries({ queryKey: ["groupJoinRequests"] });
    setGroupSuccess(message);
    setGroupError(null);
  };
  const fail = (err: Error) => setGroupError(err.message);

  const createGroupMutation = useMutation({
    mutationFn: (data: { name: string; description?: string }) => api.createGroup(data),
    onSuccess: () => {
      setShowCreateDialog(false);
      refresh(tg("createSuccess"));
    },
    onError: fail,
  });
  const requestJoinMutation = useMutation({
    mutationFn: (groupId: number) => api.requestGroupJoin(groupId),
    onSuccess: () => refresh(tg("requestSent")),
    onError: fail,
  });
  const cancelRequestMutation = useMutation({
    mutationFn: (request: JoinRequest) => api.cancelJoinRequest(request.group_id, request.id),
    onSuccess: () => refresh(tg("requestCancelled")),
    onError: fail,
  });
  const leaveGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.leaveGroup(groupId),
    onSuccess: () => {
      setConfirmLeaveId(null);
      refresh(tg("leaveSuccess"));
    },
    onError: fail,
  });
  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.deleteGroup(groupId),
    onSuccess: () => {
      setConfirmDeleteId(null);
      refresh(tg("deleteSuccess"));
    },
    onError: fail,
  });
  const updateGroupMutation = useMutation({
    mutationFn: ({ groupId, data }: { groupId: number; data: { name?: string; description?: string } }) =>
      api.updateGroup(groupId, data),
    onSuccess: () => {
      setEditGroup(null);
      refresh(tg("updateSuccess"));
    },
    onError: fail,
  });
  const kickMemberMutation = useMutation({
    mutationFn: ({ groupId, userId }: { groupId: number; userId: number }) =>
      api.kickMember(groupId, userId),
    onSuccess: () => {
      setConfirmKick(null);
      refresh(tg("kickSuccess"));
    },
    onError: fail,
  });

  return (
    <>
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="glass-card p-6 space-y-6"
      >
        <div className="flex items-center gap-3">
          <Users className="w-5 h-5 text-primary-400" />
          <h2 className="text-xl font-display font-semibold text-text-primary">{tg("title")}</h2>
        </div>

        {groupError && (
          <div className="flex items-center gap-2 text-accent-red text-sm">
            <AlertCircle className="w-4 h-4" />
            {groupError}
            <button
              onClick={() => setGroupError(null)}
              className="ml-auto text-text-muted hover:text-text-primary"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        {groupSuccess && (
          <div className="flex items-center gap-2 text-primary-400 text-sm">
            <Check className="w-4 h-4" />
            {groupSuccess}
          </div>
        )}

        {isLoadingMyGroups ? (
          <div className="flex justify-center py-6">
            <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />
          </div>
        ) : myGroupsFailed ? (
          /* Without this branch a failed fetch falls through to the empty state
             and tells a member of two groups, with a friendly icon, that they
             belong to none. */
          <div className="flex items-center gap-2 text-accent-red text-sm">
            <AlertCircle className="w-4 h-4" />
            {tg("loadFailed", { reason: (myGroupsError as Error)?.message ?? "" })}
          </div>
        ) : memberships.length > 0 ? (
          <div className="space-y-6">
            {memberships.map((membership) => (
              <GroupCard
                key={membership.group.id}
                membership={membership}
                currentUserId={user?.id}
                isGlobalAdmin={user?.role === "admin"}
                onEdit={() => setEditGroup(membership.group)}
                onDelete={() => setConfirmDeleteId(membership.group.id)}
                onLeave={() => setConfirmLeaveId(membership.group.id)}
                onKick={(userId) => setConfirmKick({ groupId: membership.group.id, userId })}
                onError={fail}
                onDecided={(message) => refresh(message)}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-4">
            <div className="w-14 h-14 bg-primary-500/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <Users className="w-7 h-7 text-primary-400" />
            </div>
            <p className="text-text-secondary mb-1">{tg("noGroup")}</p>
            <p className="text-sm text-text-muted">{tg("noGroupDesc")}</p>
          </div>
        )}

        {pendingRequests.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-text-secondary">{tg("myPendingRequests")}</h4>
            {pendingRequests.map((request) => (
              <div
                key={request.id}
                className="flex items-center justify-between p-3 rounded-lg border border-white/5 bg-white/[0.01]"
              >
                <span className="flex items-center gap-2 text-sm text-text-primary">
                  <Clock className="w-3.5 h-3.5 text-text-muted" />
                  {request.group_name}
                </span>
                <button
                  onClick={() => cancelRequestMutation.mutate(request)}
                  disabled={cancelRequestMutation.isPending}
                  className="text-xs text-text-muted hover:text-accent-red transition-colors"
                >
                  {tg("cancelRequest")}
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-white/5">
          <button
            onClick={() => setShowCreateDialog(true)}
            className="flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            {tg("createGroup")}
          </button>
          <button
            onClick={() => setShowBrowseGroups(!showBrowseGroups)}
            className="flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            {showBrowseGroups ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            {tg("browseGroups")}
          </button>
        </div>

        <AnimatePresence>
          {showBrowseGroups && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="space-y-2 pt-2">
                {isLoadingGroups ? (
                  <div className="flex justify-center py-4">
                    <Loader2 className="w-5 h-5 text-primary-500 animate-spin" />
                  </div>
                ) : allGroups && allGroups.length > 0 ? (
                  allGroups
                    .filter((group) => !myGroupIds.has(group.id))
                    .map((group) => {
                      const requested = pendingRequests.some((r) => r.group_id === group.id);
                      return (
                        <div
                          key={group.id}
                          className="flex items-center justify-between p-4 rounded-xl border border-white/10 bg-white/[0.02]"
                        >
                          <div>
                            <h4 className="text-sm font-medium text-text-primary">{group.name}</h4>
                            {group.description && (
                              <p className="text-xs text-text-muted mt-0.5">{group.description}</p>
                            )}
                            <div className="flex items-center gap-3 mt-1 text-xs text-text-muted">
                              <span>{tg("memberCount", { count: group.member_count })}</span>
                              <span>{tg("createdBy", { name: group.creator_name })}</span>
                            </div>
                          </div>
                          <button
                            onClick={() => requestJoinMutation.mutate(group.id)}
                            disabled={requested || requestJoinMutation.isPending}
                            className="btn-primary text-sm px-4 py-1.5 disabled:opacity-50"
                          >
                            {requested ? tg("requestPending") : tg("requestToJoin")}
                          </button>
                        </div>
                      );
                    })
                ) : (
                  <p className="text-sm text-text-muted text-center py-4">{tg("noGroupsAvailable")}</p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.section>

      <AnimatePresence>
        {showCreateDialog && (
          <CreateGroupDialog
            onClose={() => setShowCreateDialog(false)}
            onSubmit={(data) => createGroupMutation.mutate(data)}
            isPending={createGroupMutation.isPending}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {editGroup && (
          <EditGroupDialog
            group={editGroup}
            onClose={() => setEditGroup(null)}
            onSubmit={(data) => updateGroupMutation.mutate({ groupId: editGroup.id, data })}
            isPending={updateGroupMutation.isPending}
          />
        )}
      </AnimatePresence>

      <ConfirmModal
        isOpen={confirmLeaveId !== null}
        onClose={() => setConfirmLeaveId(null)}
        onConfirm={() => confirmLeaveId !== null && leaveGroupMutation.mutate(confirmLeaveId)}
        title={tg("leaveGroup")}
        message={tg("confirmLeave")}
        confirmLabel={tg("leaveGroup")}
        cancelLabel={tCommon("cancel")}
        isLoading={leaveGroupMutation.isPending}
        variant="danger"
      />

      <ConfirmModal
        isOpen={confirmDeleteId !== null}
        onClose={() => setConfirmDeleteId(null)}
        onConfirm={() => confirmDeleteId !== null && deleteGroupMutation.mutate(confirmDeleteId)}
        title={tg("deleteGroup")}
        message={tg("confirmDelete")}
        confirmLabel={tg("deleteGroup")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteGroupMutation.isPending}
        variant="danger"
      />

      <ConfirmModal
        isOpen={confirmKick !== null}
        onClose={() => setConfirmKick(null)}
        onConfirm={() => confirmKick && kickMemberMutation.mutate(confirmKick)}
        title={tg("kickMember")}
        message={tg("confirmKick")}
        confirmLabel={tg("kickMember")}
        cancelLabel={tCommon("cancel")}
        isLoading={kickMemberMutation.isPending}
        variant="danger"
      />
    </>
  );
}

/** The admin/member pill. Rendered for the caller's own role and for each member. */
function RoleBadge({ role }: { role: string }): JSX.Element {
  const tg = useTranslations("groups");
  const isAdmin = role === "admin";

  return (
    <span
      className={`px-1.5 py-0.5 text-xs rounded ${
        isAdmin ? "bg-primary-500/20 text-primary-400" : "bg-white/5 text-text-muted"
      }`}
    >
      {isAdmin ? tg("roleAdmin") : tg("roleMember")}
    </span>
  );
}

function GroupCard({
  membership,
  currentUserId,
  isGlobalAdmin,
  onEdit,
  onDelete,
  onLeave,
  onKick,
  onError,
  onDecided,
}: {
  membership: MyGroupMembership;
  currentUserId?: number;
  isGlobalAdmin: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onLeave: () => void;
  onKick: (userId: number) => void;
  onError: (err: Error) => void;
  onDecided: (message: string) => void;
}): JSX.Element {
  const tg = useTranslations("groups");
  const { group, role } = membership;
  // Same rule as the backend's require_group_admin: the role, or a global admin.
  // Checking created_by_user_id here is what left every non-founder admin looking
  // at buttons the server would have refused.
  const canAdminister = role === "admin" || isGlobalAdmin;

  const { data: requests } = useQuery({
    queryKey: ["groupJoinRequests", group.id],
    queryFn: () => api.listGroupJoinRequests(group.id),
    enabled: canAdminister,
  });
  const queryClient = useQueryClient();

  const decide = useMutation({
    mutationFn: async ({ requestId, approve }: { requestId: number; approve: boolean }) => {
      if (approve) {
        await api.approveJoinRequest(group.id, requestId);
      } else {
        await api.rejectJoinRequest(group.id, requestId);
      }
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["groupJoinRequests", group.id] });
      onDecided(variables.approve ? tg("approveSuccess") : tg("rejectSuccess"));
    },
    onError: onError,
  });

  const pending = (requests?.items ?? []).filter((r) => r.status === "pending");

  return (
    <div className="p-4 rounded-xl border border-white/10 bg-white/[0.02] space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-display font-semibold text-text-primary">{group.name}</h3>
            <RoleBadge role={role} />
          </div>
          {group.description && (
            <p className="text-sm text-text-secondary mt-1">{group.description}</p>
          )}
        </div>
        {canAdminister && (
          <div className="flex items-center gap-2">
            <button
              onClick={onEdit}
              className="p-2 hover:bg-white/5 rounded-lg transition-colors text-text-muted hover:text-primary-400"
              title={tg("editGroup")}
            >
              <Pencil className="w-4 h-4" />
            </button>
            <button
              onClick={onDelete}
              className="p-2 hover:bg-accent-red/10 rounded-lg transition-colors text-text-muted hover:text-accent-red"
              title={tg("deleteGroup")}
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 text-sm text-text-muted">
        <span className="flex items-center gap-1.5">
          <Users className="w-3.5 h-3.5" />
          {tg("memberCount", { count: group.member_count })}
        </span>
        <span className="flex items-center gap-1.5">
          <Crown className="w-3.5 h-3.5" />
          {tg("createdBy", { name: group.creator_name })}
        </span>
      </div>

      {canAdminister && pending.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-text-secondary">
            {tg("pendingRequests", { count: pending.length })}
          </h4>
          {pending.map((request) => (
            <div
              key={request.id}
              className="flex items-center justify-between p-3 rounded-lg border border-primary-500/20 bg-primary-500/[0.04]"
            >
              <div>
                <span className="text-sm font-medium text-text-primary">{request.user_name}</span>
                <span className="text-xs text-text-muted ml-2">{request.user_email}</span>
                {request.message && (
                  <p className="text-xs text-text-muted mt-0.5">{request.message}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => decide.mutate({ requestId: request.id, approve: true })}
                  disabled={decide.isPending}
                  className="btn-primary text-xs px-3 py-1"
                >
                  {tg("approve")}
                </button>
                <button
                  onClick={() => decide.mutate({ requestId: request.id, approve: false })}
                  disabled={decide.isPending}
                  className="text-xs text-text-muted hover:text-accent-red transition-colors px-2"
                >
                  {tg("reject")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        {group.members.map((member) => (
          <div
            key={member.id}
            className="flex items-center justify-between p-3 rounded-lg border border-white/5 bg-white/[0.01]"
          >
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-primary-500/20 flex items-center justify-center">
                <User className="w-4 h-4 text-primary-400" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-text-primary">{member.user_name}</span>
                  <RoleBadge role={member.role} />
                </div>
                <span className="text-xs text-text-muted">{member.user_email}</span>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-text-muted">
                {new Date(member.joined_at).toLocaleDateString()}
              </span>
              {canAdminister && member.user_id !== currentUserId && (
                <button
                  onClick={() => onKick(member.user_id)}
                  className="p-1.5 hover:bg-accent-red/10 rounded-lg transition-colors text-text-muted hover:text-accent-red"
                  title={tg("kickMember")}
                >
                  <UserMinus className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="pt-2 border-t border-white/5">
        <button
          onClick={onLeave}
          className="flex items-center gap-2 text-sm text-accent-red hover:text-accent-red/80 transition-colors"
        >
          <LogOut className="w-4 h-4" />
          {tg("leaveGroup")}
        </button>
      </div>
    </div>
  );
}
