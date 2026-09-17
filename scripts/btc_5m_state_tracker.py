#!/usr/bin/env python3
"""
State tracker for BTC 5m trading: one ticket per bucket, daily loss limits.
"""

import json
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class TicketState:
    """State for one ticket (one 5m bucket)."""
    bucket: int
    market_slug: str
    opened_at: float
    side: str
    entry_price: float
    shares: float
    cost_usdc: float
    token_id: str
    status: str  # 'open', 'closed', 'failed'


@dataclass
class DailyState:
    """Daily trading state."""
    date: str  # YYYY-MM-DD UTC
    trades_count: int
    realized_pnl_usdc: float
    active_tickets: dict[int, TicketState]  # bucket -> ticket
    closed_tickets: list[TicketState]


class StateTracker:
    """
    Track trading state: active tickets, daily PnL, limits.
    
    Enforces:
    - One ticket per 5m bucket
    - Daily loss limit
    - Max trades per day
    """
    
    def __init__(self, state_dir: Path, daily_loss_limit_usd: float = 50.0, max_trades_per_day: int = 20):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.daily_loss_limit_usd = daily_loss_limit_usd
        self.max_trades_per_day = max_trades_per_day
        self._state: Optional[DailyState] = None
    
    def get_current_date_utc(self) -> str:
        """Get current UTC date as YYYY-MM-DD."""
        return time.strftime('%Y-%m-%d', time.gmtime())
    
    def _state_file(self, date: str) -> Path:
        """Get state file path for a date."""
        return self.state_dir / f'state_{date}.json'
    
    def load_state(self) -> DailyState:
        """Load or create today's state."""
        today = self.get_current_date_utc()
        
        if self._state and self._state.date == today:
            return self._state
        
        state_file = self._state_file(today)
        
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    data = json.load(f)
                
                active_tickets = {
                    int(k): TicketState(**v)
                    for k, v in data.get('active_tickets', {}).items()
                }
                closed_tickets = [
                    TicketState(**t)
                    for t in data.get('closed_tickets', [])
                ]
                
                self._state = DailyState(
                    date=data['date'],
                    trades_count=data['trades_count'],
                    realized_pnl_usdc=data['realized_pnl_usdc'],
                    active_tickets=active_tickets,
                    closed_tickets=closed_tickets
                )
            except Exception:
                self._state = DailyState(
                    date=today,
                    trades_count=0,
                    realized_pnl_usdc=0.0,
                    active_tickets={},
                    closed_tickets=[]
                )
        else:
            self._state = DailyState(
                date=today,
                trades_count=0,
                realized_pnl_usdc=0.0,
                active_tickets={},
                closed_tickets=[]
            )
        
        return self._state
    
    def save_state(self):
        """Save current state to disk."""
        if not self._state:
            return
        
        state_file = self._state_file(self._state.date)
        
        data = {
            'date': self._state.date,
            'trades_count': self._state.trades_count,
            'realized_pnl_usdc': self._state.realized_pnl_usdc,
            'active_tickets': {
                str(k): asdict(v)
                for k, v in self._state.active_tickets.items()
            },
            'closed_tickets': [
                asdict(t)
                for t in self._state.closed_tickets
            ]
        }
        
        with open(state_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def can_open_ticket(self, bucket: int) -> tuple[bool, str]:
        """
        Check if we can open a ticket for this bucket.
        
        Returns:
            (can_open, reason)
        """
        state = self.load_state()
        
        # Check if ticket already exists for this bucket
        if bucket in state.active_tickets:
            return False, f"ticket_already_open_for_bucket_{bucket}"
        
        # Check daily trade limit
        if state.trades_count >= self.max_trades_per_day:
            return False, f"daily_trade_limit_reached_{state.trades_count}/{self.max_trades_per_day}"
        
        # Check daily loss limit
        if state.realized_pnl_usdc < -self.daily_loss_limit_usd:
            return False, f"daily_loss_limit_exceeded_{state.realized_pnl_usdc:.2f}<-{self.daily_loss_limit_usd}"
        
        return True, "ok"
    
    def open_ticket(self, ticket: TicketState):
        """Register a new open ticket."""
        state = self.load_state()
        state.active_tickets[ticket.bucket] = ticket
        state.trades_count += 1
        self.save_state()
    
    def close_ticket(self, bucket: int, pnl_usdc: float, status: str = 'closed'):
        """Close a ticket and update PnL."""
        state = self.load_state()
        
        if bucket not in state.active_tickets:
            return
        
        ticket = state.active_tickets.pop(bucket)
        ticket.status = status
        state.closed_tickets.append(ticket)
        state.realized_pnl_usdc += pnl_usdc
        self.save_state()
    
    def get_daily_summary(self) -> dict:
        """Get summary of today's trading."""
        state = self.load_state()
        
        return {
            'date': state.date,
            'trades_count': state.trades_count,
            'max_trades_per_day': self.max_trades_per_day,
            'realized_pnl_usdc': state.realized_pnl_usdc,
            'daily_loss_limit_usd': self.daily_loss_limit_usd,
            'active_tickets_count': len(state.active_tickets),
            'active_buckets': sorted(state.active_tickets.keys()),
            'can_trade': state.trades_count < self.max_trades_per_day and state.realized_pnl_usdc >= -self.daily_loss_limit_usd
        }
