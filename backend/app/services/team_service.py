from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.scan_history import ScanHistory
from app.models.team import Team, TeamMember, TeamMemberRole
from app.models.user import User


class TeamService:
    def __init__(self, db: Session):
        self.db = db

    def create_team(self, name: str, creator_id: int) -> Team:
        existing = self.db.scalar(select(Team).where(Team.name == name))
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Team name already exists")

        team = Team(name=name, created_by_id=creator_id)
        self.db.add(team)
        self.db.flush()  # Get team.id for membership

        owner_member = TeamMember(team_id=team.id, user_id=creator_id, role=TeamMemberRole.OWNER)
        self.db.add(owner_member)
        self.db.commit()
        self.db.refresh(team)
        return team

    def add_member(self, team_id: int, operator_id: int, user_id: int, role: TeamMemberRole) -> TeamMember:
        team = self.db.get(Team, team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        operator_member = self.db.scalar(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == operator_id)
        )
        if operator_member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a team member")
        if operator_member.role not in {TeamMemberRole.OWNER, TeamMemberRole.ADMIN}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient team role")

        target_user = self.db.get(User, user_id)
        if target_user is None or not target_user.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        existing_member = self.db.scalar(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
        if existing_member:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already in team")

        member = TeamMember(team_id=team_id, user_id=user_id, role=role)
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        return member

    def ensure_membership(self, team_id: int, user_id: int) -> TeamMember:
        team = self.db.get(Team, team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        member = self.db.scalar(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
        if member is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a team member")
        return member

    def get_team_stats(self, team_id: int, user_id: int, start: datetime | None, end: datetime | None):
        self.ensure_membership(team_id, user_id)

        detections_query = (
            select(
                func.date_trunc("day", ScanHistory.created_at).label("day"),
                func.count(ScanHistory.id).label("count"),
            )
            .join(TeamMember, TeamMember.user_id == ScanHistory.user_id)
            .where(TeamMember.team_id == team_id)
        )

        if start:
            detections_query = detections_query.where(ScanHistory.created_at >= start)
        if end:
            detections_query = detections_query.where(ScanHistory.created_at <= end)

        detections_query = detections_query.group_by("day").order_by("day")
        rows = self.db.execute(detections_query).all()
        return rows
