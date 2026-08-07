#ifndef AE829D70_4328_4F50_8735_1D6BF2080703
#define AE829D70_4328_4F50_8735_1D6BF2080703
#include "TypeDefine.h"
namespace basic
{
    namespace mathtool
    {
        void filtfilt(vectord B, vectord A, const vectord &X, vectord &Y);
        void filter(vectord B, vectord A, const vectord &X, vectord &Y, vectord &Zi);
    }
}
#endif /* AE829D70_4328_4F50_8735_1D6BF2080703 */
