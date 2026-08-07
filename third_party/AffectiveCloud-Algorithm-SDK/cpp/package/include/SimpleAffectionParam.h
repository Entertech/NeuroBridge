#ifndef DC76F83C_E1D2_4D04_952B_64A79757B352
#define DC76F83C_E1D2_4D04_952B_64A79757B352
#include "NumCpp.hpp"
#include "FeatureRulerParams.h"
#include <vector>
namespace basic
{
    namespace affection
    {
        namespace params
        {

            const std::vector<nc::NdArray<double>> relaxationRuler = {
                alphaNormRuler, gammaNormRuler, alphaAsyRuler};

            const std::vector<nc::NdArray<double>> pleasureRuler = {
                alphaAsyRuler, thetaAsyRuler};

            const std::vector<nc::NdArray<double>> attentionRuler = {
                betaThetaRateruler, gammaNormRuler, alphaNormRuler};

            const std::vector<nc::NdArray<double>> meditationRuler = {
                alphaNormRuler, thetaNormRuler, gammaNormRuler, alphaAsyRuler};
        }
    }
}

#endif /* DC76F83C_E1D2_4D04_952B_64A79757B352 */
