from django import forms

from .models import Review

RATING_CHOICES = [(i, f"{i} star{'s' if i > 1 else ''}") for i in range(1, 6)]


class ReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=RATING_CHOICES,
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={"required": "Please choose a star rating.", "invalid_choice": "Please choose 1 to 5 stars."},
    )

    class Meta:
        model = Review
        fields = ["rating", "title", "body"]
        labels = {"title": "Title (optional)", "body": "Your review"}
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Sum it up in a few words"}),
            "body": forms.Textarea(attrs={"rows": 4, "placeholder": "What did you like or dislike?"}),
        }

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if len(body) < 10:
            raise forms.ValidationError("Please write at least 10 characters.")
        return body
