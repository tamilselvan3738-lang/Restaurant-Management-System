from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class CustomerReview(models.Model):
    restaurant = models.ForeignKey(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reviews',
        help_text="Restaurant tenant this review belongs to"
    )
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField(blank=True)
    rating = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Overall rating between 1 and 5"
    )
    food_rating = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(5)])
    service_rating = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(5)])
    ambiance_rating = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField()
    dish_recommended = models.CharField(max_length=150, blank=True, help_text="Customer favorite dish recommendation")
    is_verified = models.BooleanField(default=True, help_text="Verified dining guest")
    response = models.TextField(blank=True, help_text="Official management response")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Customer Review'
        verbose_name_plural = 'Customer Reviews'

    def __str__(self):
        return f"{self.customer_name} - {self.rating}★"
