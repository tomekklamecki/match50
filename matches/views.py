from django.shortcuts import render, redirect

from .models import Match, Round, Prediction


def home(request):
    return render(
        request,
        "matches/home.html"
    )


def obstaw(request):

    active_round = Round.objects.get(
        is_active=True
    )


    matches = active_round.matches.order_by(
        "kickoff"
    )


    # =========================
    # ZAPISYWANIE TYPÓW
    # =========================

    if request.method == "POST":


        # Niezalogowany użytkownik
        # nie może zapisywać typów.

        if not request.user.is_authenticated:

            return redirect("login")


        for match in matches:


            predicted_result = request.POST.get(
                f"prediction_{match.id}"
            )


            if predicted_result in ["1", "X", "2"]:


                Prediction.objects.update_or_create(

                    user=request.user,

                    match=match,

                    defaults={
                        "predicted_result":
                            predicted_result
                    }

                )


        return redirect("obstaw")


    # =========================
    # POBIERANIE ZAPISANYCH TYPÓW
    # =========================

    if request.user.is_authenticated:

        predictions = Prediction.objects.filter(

            user=request.user,

            match__round=active_round

        )

    else:

        # Niezalogowany nie ma zapisanych typów.

        predictions = []


    predictions_dict = {

        prediction.match_id:
            prediction.predicted_result

        for prediction in predictions

    }


    # =========================
    # PRZEKAZANIE TYPÓW DO TEMPLATE
    # =========================

    for match in matches:

        match.saved_prediction = (
            predictions_dict.get(match.id)
        )


    return render(

        request,

        "matches/obstaw.html",

        {
            "matches": matches,
            "active_round": active_round,
        }

    )