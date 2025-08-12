from .agents import (
    pdf_to_pptx_builder,
    word_to_pptx_builder,
    pptx_to_pdf_builder,
    pptx_to_docx_builder,
    pptx_to_html5_builder,
)

def register():
    # materialized mapping used by get_registry()
    return {
        "office": {
            "pdf_to_pptx":    pdf_to_pptx_builder,
            "word_to_pptx":   word_to_pptx_builder,
            "pptx_to_pdf":    pptx_to_pdf_builder,
            "pptx_to_docx":   pptx_to_docx_builder,
            "pptx_to_html5":  pptx_to_html5_builder,
        }
    }

# from .agents import (
#     word_to_pptx, pptx_to_docx, pdf_to_pptx,
#     pptx_to_pdf, pptx_to_html5
# )

# def register():
#     return {
#         "office": {
#             "word_to_pptx": word_to_pptx,
#             "pptx_to_docx": pptx_to_docx,
#             "pdf_to_pptx": pdf_to_pptx,
#             "pptx_to_pdf": pptx_to_pdf,
#             "pptx_to_html5": pptx_to_html5,
#         }
#     }
