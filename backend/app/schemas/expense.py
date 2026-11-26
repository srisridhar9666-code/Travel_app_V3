from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

from app.core.enums import ExpenseCategory, ExpenseStatus, PaymentMode


class ExpenseItemCreate(BaseModel):
    expense_date: date
    category: ExpenseCategory = ExpenseCategory.FOOD
    vendor_merchant_name: str | None = None
    bill_number: str | None = None
    bill_date: date | None = None
    claimed_amount: float = Field(..., gt=0)
    receipt_url: str | None = None
    is_receipt_attached: bool = False
    justification: str | None = None

    @model_validator(mode="after")
    def validate_policy_caps(self) -> "ExpenseItemCreate":
        # Keep flexible for user submissions
        return self


class ExpenseItemRead(BaseModel):
    id: int
    claim_id: int
    expense_date: date
    category: ExpenseCategory
    vendor_merchant_name: str | None = None
    bill_number: str | None = None
    bill_date: date | None = None
    claimed_amount: float
    approved_amount: float = 0.0
    deducted_amount: float = 0.0
    deduction_reason: str | None = None
    receipt_url: str | None = None
    is_receipt_attached: bool = False
    justification: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ExpenseClaimCreate(BaseModel):
    project_id: int
    request_id: int | None = None
    destination_city: str = Field(..., min_length=1, max_length=120)
    travel_start_date: date
    travel_end_date: date

    # Form fields
    employee_payment_mode: str | None = Field(None, description="ONLINE or CASH")
    expense_type: str | None = Field(None, description="FOOD, TRAVEL, or OTHERS")
    expense_type_other: str | None = Field(None, description="Specified when expense_type is OTHERS")
    remarks: str | None = None

    # Payout Details
    upi_id: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder_name: str | None = None
    ifsc_code: str | None = None

    # Attachment URL (images, PDFs)
    attachment_url: str | None = None
    total_amount: float | None = None

    items: list[ExpenseItemCreate] = Field(default_factory=list)


class ExpenseClaimRead(BaseModel):
    id: int
    claim_code: str
    user_id: int
    user_name: str | None = None
    user_email: str | None = None
    project_id: int
    project_name: str | None = None
    request_id: int | None = None
    destination_city: str
    travel_start_date: date
    travel_end_date: date

    # Form fields
    employee_payment_mode: str | None = None
    expense_type: str | None = None
    expense_type_other: str | None = None
    remarks: str | None = None

    # Payout Details
    upi_id: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder_name: str | None = None
    ifsc_code: str | None = None
    attachment_url: str | None = None

    status: ExpenseStatus
    total_claimed_amount: float
    total_approved_amount: float = 0.0
    total_deducted_amount: float = 0.0
    manager_id: int | None = None
    manager_name: str | None = None
    manager_reviewed_at: datetime | None = None
    manager_notes: str | None = None
    manager_endorsed: bool = False
    finance_auditor_id: int | None = None
    finance_auditor_name: str | None = None
    finance_audited_at: datetime | None = None
    finance_deduction_notes: str | None = None
    payment_mode: PaymentMode | None = None
    payment_date: date | None = None
    bank_utr_number: str | None = None
    settled_at: datetime | None = None
    settled_by_id: int | None = None
    settled_by_name: str | None = None
    items: list[ExpenseItemRead] = []
    is_food_cap_exceeded: bool = False
    food_excess_amount: float = 0.0
    is_unbilled_transit_exceeded: bool = False
    unbilled_transit_excess_amount: float = 0.0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ManagerEndorsement(BaseModel):
    endorsed: bool
    notes: str | None = None


class ItemDeduction(BaseModel):
    item_id: int
    deducted_amount: float = Field(..., gt=0)
    deduction_reason: str = Field(..., min_length=2, max_length=300)


class FinanceAudit(BaseModel):
    decision: ExpenseStatus = Field(..., description="FINANCE_APPROVED, PARTIALLY_APPROVED, or REJECTED")
    deductions: list[ItemDeduction] = []
    notes: str | None = None


class DisbursementSettlement(BaseModel):
    payment_mode: PaymentMode
    payment_date: date
    bank_utr_number: str = Field(..., min_length=4, max_length=80, description="Bank Transaction UTR / Ref No")
